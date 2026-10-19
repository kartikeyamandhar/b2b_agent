"""RFC 7807 problem details, correlation ids, and request logging."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from recallops.logs import get_logger, log_event

log = get_logger("api")
PROBLEM_JSON = "application/problem+json"


class NotYetImplemented(Exception):
    """A route whose contract is final but whose implementation arrives in a later milestone."""

    def __init__(self, route: str, milestone: str, owner: str = "backend owner") -> None:
        super().__init__(route)
        self.detail = f"{route} arrives in {milestone} ({owner}); the contract in docs/contracts/openapi.json is final."


class PaymentRequired(Exception):
    """The billing gate refused an action (03-payments.md section 2): the UI turns this into an upgrade prompt."""

    def __init__(self, action: str, reason: str | None) -> None:
        super().__init__(action)
        self.detail = f"{action} is not included in the current plan: {reason or 'upgrade to continue'}."


def problem(status: int, title: str, detail: str | None, request: Request) -> JSONResponse:
    body = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
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
    429: "Too Many Requests",
    501: "Not Implemented",
}


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def correlation(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.correlation_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        t0 = time.monotonic()
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.correlation_id
        log_event(
            log,
            "http_request",
            method=request.method,
            path=request.url.path,
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
        return problem(422, TITLES[422], str(exc.errors())[:1000], request)

    @app.exception_handler(NotYetImplemented)
    async def not_yet(request: Request, exc: NotYetImplemented) -> JSONResponse:
        return problem(501, TITLES[501], exc.detail, request)

    @app.exception_handler(PaymentRequired)
    async def payment_required(request: Request, exc: PaymentRequired) -> JSONResponse:
        return problem(402, TITLES[402], exc.detail, request)
