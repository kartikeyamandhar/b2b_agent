// Phase C: reissuing one claim's portal link from claim detail (admin).
import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import ClaimPage from "@/app/app/claims/[id]/page";
import { ReissueLink } from "@/components/ReissueLink";
import { reissueAvailability, reissuePath } from "@/lib/claim-link";

const claim = JSON.parse(readFileSync(new URL("./fixtures/claim-detail.json", import.meta.url), "utf8")) as Record<string, unknown>;
const C = claim.id as string;
const programFixture = (JSON.parse(readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8")) as { items: Record<string, unknown>[] }).items[0]!;
let programStatus = "open";

let calls: { url: string; method: string }[];
beforeEach(() => {
  calls = [];
  programStatus = "open";
  vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
  vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, method: init.method ?? "GET" });
    if (url.endsWith("/trace")) return Response.json({ title: "Not Found", status: 404, detail: "no run for this claim" }, { status: 404 });
    if (url.includes("/v1/programs/")) return Response.json({ ...programFixture, id: claim.program_id, status: programStatus });
    return Response.json(claim);
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("reissue rules", () => {
  it("lets an admin reissue any claim the backend accepts", () => {
    for (const s of ["draft", "submitted", "needs_info", "flagged", "approved", "denied", "under_review", "capped"]) expect(reissueAvailability(s, true)).toEqual({ allowed: true });
  });

  it("refuses every claim of a closed program, and leaves an unknown program to the API", () => {
    expect(reissueAvailability("draft", true, "closed")).toMatchObject({ allowed: false, reason: expect.stringContaining("program is closed") });
    expect(reissueAvailability("draft", true, null)).toEqual({ allowed: true });
    expect(reissueAvailability("draft", true, "open")).toEqual({ allowed: true });
  });

  it("refuses paid and closed claims and reviewers, with a reason", () => {
    for (const s of ["paid", "closed"]) expect(reissueAvailability(s, true)).toMatchObject({ allowed: false, reason: expect.stringContaining(s) });
    expect(reissueAvailability("flagged", false)).toMatchObject({ allowed: false, reason: expect.stringContaining("admin") });
  });

  it("only posts to a well-formed claim id", () => {
    expect(reissuePath(C)).toBe(`/v1/claims/${C}/link`);
    expect(reissuePath("../programs")).toBeNull();
  });
});

describe("ReissueLink", () => {
  it("offers the reissue behind a confirmation and shows no link until one is issued", () => {
    const html = renderToStaticMarkup(<ReissueLink claimId={C} status="needs_info" canAdmin />);
    expect(html).toContain("data-reissue-start");
    expect(html).toContain("The old link stops working at once");
    expect(html).not.toContain("data-reissue-confirm");
    expect(html).not.toContain("data-reissued");
  });

  it("explains why it is unavailable", () => {
    expect(renderToStaticMarkup(<ReissueLink claimId={C} status="paid" canAdmin />)).toContain("data-reissue-blocked");
    expect(renderToStaticMarkup(<ReissueLink claimId={C} status="flagged" canAdmin={false} />)).toContain("Only an admin");
  });
});

describe("/app/claims/[id]", () => {
  const render = async () =>
    renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{await ClaimPage({ params: Promise.resolve({ id: C }) })}</QueryClientProvider>);

  it("shows the reissue control to an admin, and never calls the reissue route while rendering", async () => {
    const html = await render();
    expect(html).toContain('id="claim-link"');
    expect(calls.every((c) => c.method === "GET")).toBe(true);
    expect(calls.some((c) => c.url.endsWith("/link"))).toBe(false);
  });

  it("reads the claim's program and blocks the reissue when it is closed", async () => {
    programStatus = "closed";
    const html = await render();
    expect(calls.some((c) => c.url.endsWith(`/v1/programs/${claim.program_id as string}`))).toBe(true);
    expect(html).toContain("data-reissue-blocked");
    expect(html).toContain("The program is closed");
    expect(html).not.toContain("data-reissue-start");
  });

  it("does not read the program for a reviewer", async () => {
    vi.stubEnv("RECALLOPS_DEV_ROLE", "reviewer");
    await render();
    expect(calls.some((c) => c.url.includes("/v1/programs/"))).toBe(false);
  });

  it("shows a reviewer why they cannot reissue", async () => {
    vi.stubEnv("RECALLOPS_DEV_ROLE", "reviewer");
    expect(await render()).toContain("Only an admin can issue a new claim link");
  });
});
