import { describe, expect, it } from "vitest";

import { humanSentence, problemFrom } from "@/lib/api/problem";

describe("problemFrom", () => {
  it("reads an RFC 7807 problem", () => {
    const p = problemFrom(409, { type: "about:blank", title: "Conflict", status: 409, detail: "a review is already pending", correlation_id: "c-1" });
    expect(p).toEqual({ status: 409, title: "Conflict", detail: "a review is already pending", correlationId: "c-1", retryAfterSeconds: null });
  });

  it("flattens FastAPI's 422 list without the body prefix", () => {
    const p = problemFrom(422, { detail: [{ loc: ["body", "claimant", "zip"], msg: "Field required", type: "missing" }, { loc: ["query", "limit"], msg: "too big", type: "x" }] });
    expect(p.detail).toBe("claimant.zip: Field required; query.limit: too big");
  });

  it("falls back to the x-request-id header and reads Retry-After", () => {
    const p = problemFrom(429, "Too Many Requests", new Headers({ "x-request-id": "rid-9", "retry-after": "120" }));
    expect(p.correlationId).toBe("rid-9");
    expect(p.retryAfterSeconds).toBe(120);
    expect(p.detail).toBeNull();
  });

  it("prefers the body's correlation id over the header", () => {
    expect(problemFrom(500, { title: "x", status: 500, correlation_id: "body-id" }, new Headers({ "x-request-id": "hdr" })).correlationId).toBe("body-id");
  });

  it("ignores a non-numeric Retry-After", () => {
    expect(problemFrom(429, null, new Headers({ "retry-after": "soon" })).retryAfterSeconds).toBeNull();
  });
});

describe("humanSentence", () => {
  it.each([400, 401, 402, 403, 404, 409, 413, 422, 429, 500, 502, 503, 504, 0])("has a sentence for %i", (status) => {
    expect(humanSentence(status).length).toBeGreaterThan(10);
  });

  it("tells a 409 to refresh and a 503 that nothing was lost", () => {
    expect(humanSentence(409)).toMatch(/Refresh/);
    expect(humanSentence(503)).toMatch(/Nothing was lost/);
  });
});
