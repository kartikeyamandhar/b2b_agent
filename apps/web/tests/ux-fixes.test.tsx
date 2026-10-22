import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import * as BillingRoute from "@/app/app/billing/page";
import * as ClaimRoute from "@/app/app/claims/[id]/page";
import * as ProgramsRoute from "@/app/app/programs/page";
import { metadata as rootMetadata } from "@/app/layout";
import { ClaimDetailLive } from "@/components/ClaimDetailLive";
import { ClaimsTable } from "@/components/ClaimsTable";
import { ProblemAlert } from "@/components/ProblemAlert";
import { ProgramsTable } from "@/components/ProgramsTable";
import { StatusView } from "@/components/portal/StatusView";
import { CONSOLE_NAV } from "@/components/AppShell";
import { ClaimDetail, ClaimPage, PortalView, ProgramList } from "@/lib/api-types";
import { methodAfterPayoutError } from "@/lib/portal";

const read = (f: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));

describe("phone cards", () => {
  it("lists every claim as a card with a link, marking those that need a reviewer", () => {
    const page = ClaimPage.parse(read("claims-page.json"));
    const out = renderToStaticMarkup(<ClaimsTable claims={page.items} />);
    expect(out.match(/data-card-claim-id=/g)).toHaveLength(page.items.length);
    expect(out).toContain("md:hidden");
    expect(out).toMatch(/class="hidden [^"]*\bmd:block\b[^"]*"/);
    const card = out.split("<li").find((l) => l.includes('data-card-claim-id="66dcc492'))!;
    expect(card).toContain("border-l-warn");
    expect(card).toContain("Needs a reviewer");
  });

  it("shows program figures as cards", () => {
    const out = renderToStaticMarkup(<ProgramsTable programs={ProgramList.parse(read("programs.json")).items} />);
    expect(out).toContain('aria-label="Recall programs"');
    expect(out).toContain("Awaiting review");
  });
});

describe("claim detail on phones", () => {
  it("puts the decision and reviewer actions first below the large breakpoint", () => {
    const claim = ClaimDetail.parse(read("claim-detail.json"));
    const out = renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}><ClaimDetailLive initialClaim={claim} initialTrace={null} loadedAt={Date.now()} /></QueryClientProvider>);
    const col = out.split('class="').find((c) => c.startsWith("order-first"));
    expect(col).toContain("lg:order-none");
  });
});

describe("portal copy", () => {
  const view = PortalView.parse(read("portal-view.json"));

  it("does not repeat the approval in a Details list or the payout prompt twice", () => {
    const out = renderToStaticMarkup(<StatusView token={"t".repeat(20)} view={{ ...view, status: "approved", payout_status: "pending", decision_reasons: ["Your claim was approved."] }} />);
    expect(out).not.toContain(">Details<");
    expect(out.match(/receive your refund/g)!.length).toBeLessThanOrEqual(2);
  });

  it("keeps the reasons for a denial", () => {
    const out = renderToStaticMarkup(<StatusView token={"t".repeat(20)} view={{ ...view, status: "denied", decision_reasons: ["The model is not part of this recall."] }} />);
    expect(out).toContain(">Why<");
  });

  it("can hide the API's technical detail from claimants but keeps the reference", () => {
    const out = renderToStaticMarkup(<ProblemAlert problem={{ status: 404, title: "Not Found", detail: "claim link not found or expired", correlationId: "c-1", retryAfterSeconds: null }} sentence="This claim link is not valid." showDetail={false} />);
    expect(out).not.toContain("claim link not found or expired");
    expect(out).toContain("c-1");
  });

  it("switches the payout form to a mailed check when the plan pays by check (402)", () => {
    expect(methodAfterPayoutError(402, "stripe_connect")).toBe("check_export");
    expect(methodAfterPayoutError(400, "stripe_connect")).toBe("stripe_connect");
    expect(methodAfterPayoutError(503, "check_export")).toBe("check_export");
  });
});

describe("navigation and titles", () => {
  it("links Billing from the console navigation", () => {
    expect(CONSOLE_NAV.map((n) => n.href)).toEqual(["/app/programs", "/app/payouts", "/app/billing", "/app/access"]);
  });

  it("gives each page its own title under the RecallOps template", () => {
    expect(rootMetadata.title).toEqual({ default: "RecallOps", template: "%s · RecallOps" });
    expect(ProgramsRoute.metadata).toEqual({ title: "Programs" });
    expect(ClaimRoute.metadata).toEqual({ title: "Claim" });
    expect(BillingRoute.metadata).toEqual({ title: "Billing" });
  });
});
