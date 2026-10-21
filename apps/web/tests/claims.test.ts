import { describe, expect, it } from "vitest";

import * as api from "@/lib/api-types";
import { REVIEW_LABEL, claimsApiPath, parseQueueParams, queueHref, reviewState, shortId } from "@/lib/claims";

const P = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";
const CURSOR = "MjAyNi0xMC0yMFQwODowMTowMCswMDowMHxhZDRi";

describe("parseQueueParams", () => {
  it("accepts every contract status and an API-shaped cursor", () => {
    for (const s of api.ClaimSummaryStatusValues) expect(parseQueueParams({ status: s }).status).toBe(s);
    expect(parseQueueParams({ cursor: `${CURSOR}==` })).toMatchObject({ cursor: `${CURSOR}==`, ignoredCursor: false });
  });

  it("ignores an unknown status and says which one", () => {
    expect(parseQueueParams({ status: "archived" })).toEqual({ status: null, cursor: null, ignoredStatus: "archived", ignoredCursor: false });
  });

  it("ignores a cursor the API could not have issued", () => {
    for (const bad of ["a b", "../x", "x".repeat(300), "a&limit=200"]) {
      expect(parseQueueParams({ cursor: bad })).toMatchObject({ cursor: null, ignoredCursor: true });
    }
  });

  it("uses the first value of a repeated parameter", () => {
    expect(parseQueueParams({ status: ["flagged", "denied"] }).status).toBe("flagged");
  });

  it("treats empty parameters as absent", () => {
    expect(parseQueueParams({ status: "", cursor: "" })).toEqual({ status: null, cursor: null, ignoredStatus: null, ignoredCursor: false });
  });
});

describe("URLs", () => {
  it("builds console queue links", () => {
    expect(queueHref(P, { status: null, cursor: null })).toBe(`/app/programs/${P}/claims`);
    expect(queueHref(P, { status: "flagged", cursor: CURSOR })).toBe(`/app/programs/${P}/claims?status=flagged&cursor=${CURSOR}`);
  });

  it("asks the API for one page of 50 with the filter and cursor", () => {
    expect(claimsApiPath(P, { status: null, cursor: null })).toEqual({ path: `/v1/programs/${P}/claims`, search: "?limit=50" });
    expect(claimsApiPath(P, { status: "under_review", cursor: "ab=" }).search).toBe("?status=under_review&cursor=ab%3D&limit=50");
  });
});

describe("reviewState", () => {
  it.each([
    ["flagged", false, "needs_reviewer"],
    ["under_review", false, "needs_reviewer"],
    ["capped", true, "needs_reviewer"],
    ["needs_info", false, "waiting_claimant"],
    ["submitted", false, "being_checked"],
    ["approved", true, "routed"],
    ["denied", false, "none"],
    ["paid", false, "none"],
    ["archived", false, "none"],
  ] as const)("%s (routed %s) is %s", (status, routed, state) => {
    expect(reviewState({ status, routed_to_human: routed })).toBe(state);
  });

  it("labels every state that needs one", () => {
    expect(REVIEW_LABEL.needs_reviewer).toBe("Needs a reviewer");
    expect(REVIEW_LABEL.none).toBe("");
  });

  it("shortens ids to their first 8 characters", () => {
    expect(shortId(P)).toBe("0874f66f");
  });
});
