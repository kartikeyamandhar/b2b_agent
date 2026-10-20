"""RFC 7807 problem details, correlation ids, and request logging."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from recallops.logs import get_logger, log_event

log = get_logger("api")
PROBLEM_JSON = "application/problem+json"


class PaymentRequired(Exception):
    """The billing gate refused an action (03-payments.md section 2): the UI turns this into an upgrade prompt."""

    def __init__(self, action: str, reason: str | None) -> None:
        super().__init__(action)
        self.detail = f"{action} is not included in the current plan: {reason or 'upgrade to continue'}."


def problem(status: int, title: str, detail: str | None, request: Request) -> JSONResponse:
    from recallops.api.guards import redact

    body = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": redact(request.url.path),
        "correlation_id": getattr(request.state, "correlation_id", None),
    }
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Entity",
    413: "Content Too Large",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}
INTERNAL_DETAIL = "unexpected server error; nothing was saved. Quote the correlation_id when reporting it."


def validation_detail(errors: Sequence[Any]) -> str:
    """'body.recall_id: Field required; query.limit: ...', not a Python repr; input values are never echoed."""
    parts = []
    for error in errors:
        loc = ".".join(str(part) for part in error.get("loc", ()))
        parts.append(f"{loc}: {error.get('msg', 'invalid')}" if loc else str(error.get("msg", "invalid")))
    return "; ".join(parts)[:1000] or "invalid request"


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlation(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.correlation_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        t0 = time.monotonic()
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.correlation_id
        from recallops.api.guards import redact

        log_event(
            log,
            "http_request",
            method=request.method,
            path=redact(request.url.path),
            status=response.status_code,
            correlation_id=request.state.correlation_id,
            duration_ms=round((time.monotonic() - t0) * 1000, 1),
        )
        return response

    @app.exception_handler(HTTPException)
    async def http_problem(request: Request, exc: HTTPException) -> JSONResponse:
        return problem(exc.status_code, TITLES.get(exc.status_code, "Error"), str(exc.detail), request)

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(422, TITLES[422], validation_detail(exc.errors()), request)

    import stripe

    from recallops.billing.client import PaymentError
    from recallops.payouts.db_ledger import NotPayable

    @app.exception_handler(PaymentError)
    async def payment_problem(request: Request, exc: PaymentError) -> JSONResponse:
        return problem(exc.status, TITLES.get(exc.status, "Payment integration unavailable"), exc.detail, request)

    @app.exception_handler(NotPayable)
    async def payout_problem(request: Request, exc: NotPayable) -> JSONResponse:
        return problem(409, TITLES[409], str(exc), request)

    @app.exception_handler(stripe.StripeError)
    async def stripe_problem(request: Request, exc: stripe.StripeError) -> JSONResponse:
        """Never Stripe's message (it can carry request data). A configuration error (bad key or permission, an
        invalid request such as a wrong price id) is permanent, so the caller is not told to retry it."""
        from recallops.api.guards import redact

        permanent = isinstance(exc, (stripe.AuthenticationError, stripe.PermissionError, stripe.InvalidRequestError))
        log_event(
            log,
            "stripe_request_failed",
            level=logging.WARNING,
            error=type(exc).__name__,
            code=getattr(exc, "code", None),
            http_status=getattr(exc, "http_status", None),
            request_id=getattr(exc, "request_id", None),
            path=redact(request.url.path),
            correlation_id=getattr(request.state, "correlation_id", None),
        )
        detail = (
            "the payment configuration needs operator attention"
            if permanent
            else "Stripe test-mode request failed; retry or contact the operator"
        )
        return problem(503, "Payment integration unavailable", detail, request)

    @app.exception_handler(PaymentRequired)
    async def payment_required(request: Request, exc: PaymentRequired) -> JSONResponse:
        return problem(402, TITLES[402], exc.detail, request)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Any other error, including a commit that fails as a session dependency exits: a problem detail with a
        fixed message (never the exception's text, which can carry SQL or values) and the correlation id."""
        from recallops.api.guards import redact

        log_event(
            log,
            "http_unhandled_error",
            level=logging.ERROR,
            path=redact(request.url.path),
            error=type(exc).__name__,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
        return problem(500, TITLES[500], INTERNAL_DETAIL, request)
