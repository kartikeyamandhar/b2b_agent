// Visual audit, console core: phone navigation, the claim page from 1024 px, words for backend identifiers, KPI
// alignment, photo boxes, cost formatting, the queue at laptop widths, and sentence case.
import { readFileSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({
  ...(await orig<typeof import("next/navigation")>()),
  usePathname: () => "/operator",
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }),
}));

import { AppShell, CONSOLE_NAV } from "@/components/AppShell";
import { ClaimDetailLive } from "@/components/ClaimDetailLive";
import { AuditTrail, ClaimFacts, DecisionPanel, PhotoEvidence } from "@/components/ClaimPanels";
import { ClaimsQueue, emptyQueueTitle } from "@/components/ClaimsQueue";
import { ClaimsTable } from "@/components/ClaimsTable";
import { ProgramOverview, instructionLooksCut, registrationHref } from "@/components/ProgramOverview";
import { PortfolioSummary } from "@/components/ProgramPortfolio";
import { TraceLive } from "@/components/TraceLive";
import { TraceView } from "@/components/TraceView";
import { ClaimDetail, ClaimPage, ProgramList, Trace, type Program } from "@/lib/api-types";
import { reasonParts, reasonText } from "@/lib/claim-detail";
import { auditActionLabel, decidedByLabel, graphLabel, humanize, proofSentence } from "@/lib/status";

const read = (f: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${f}`, import.meta.url), "utf8"));
const claim = ClaimDetail.parse(read("claim-detail.json"));
const trace = Trace.parse(read("trace-agent.json"));
const page = ClaimPage.parse(read("claims-page.json"));
const programs = ProgramList.parse(read("programs.json")).items;
const open = programs[0] as Program;
const withQuery = (el: ReactElement) => renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);
const OTHER = "8e223bc0-1111-4222-8333-444455556666";

describe("console navigation", () => {
  it("wraps the phone row instead of widening the page, and never splits a label", () => {
    const html = renderToStaticMarkup(<AppShell identityNote="n" role="admin">x</AppShell>);
    expect(html).toMatch(/class="flex flex-wrap gap-1 [^"]*md:block/);
    const navLinks = (html.match(/<a [^>]*>/g) ?? []).filter((l) => l.includes("rounded-md px-3 py-2"));
    expect(navLinks).toHaveLength(4);
    for (const l of navLinks) expect(l).toContain("whitespace-nowrap");
  });

  it("has no Platform group or operator item: the operator has a window of their own (plan v3)", () => {
    expect(CONSOLE_NAV.some((i) => i.href === "/operator")).toBe(false);
    const html = renderToStaticMarkup(<AppShell identityNote="n" role="admin">x</AppShell>);
    expect(html).not.toContain(">Platform<");
    expect(html).not.toContain('href="/operator"');
  });
});

describe("claim page between 1024 and 1279 px", () => {
  const html = withQuery(<ClaimDetailLive initialClaim={claim} initialTrace={null} loadedAt={Date.now()} />);

  it("keeps three columns for xl and stacks the panels below it", () => {
    expect(html).toContain("xl:grid-cols-3");
    expect(html).not.toContain("lg:grid-cols-3");
    expect(html).toContain("xl:order-none");
    expect(html).not.toContain("lg:order-none");
  });

  it("lets a photo caption shrink inside its panel and sizes the photo by the panel", () => {
    const photos = renderToStaticMarkup(<PhotoEvidence claimId={claim.id} photos={claim.photos} readings={{}} />);
    expect(photos).toContain("@container");
    expect(photos).toContain("@xs:grid-cols-[10rem_minmax(0,1fr)]");
    expect(photos).not.toContain("sm:grid-cols-[10rem_1fr]");
    expect(photos).toMatch(/<figcaption class="min-w-0/);
  });

  it("keeps the synthetic chip on one line", () => {
    const facts = renderToStaticMarkup(<ClaimFacts claim={{ ...claim, claimant: { ...claim.claimant, synthetic: true } }} />);
    expect(facts).toMatch(/class="[^"]*whitespace-nowrap[^"]*">synthetic</);
    // The label column narrows when the panel is narrow (the dl is the container).
    expect(facts).toContain('<dl class="@container">');
    expect(facts).toContain("grid-cols-[6.5rem_minmax(0,1fr)]");
    expect(facts).toContain("@xs:grid-cols-[8rem_minmax(0,1fr)]");
  });
});

describe("claim photos", () => {
  it("draws each photo in a 4:3 box, the camera's shape, without cropping it", () => {
    const html = renderToStaticMarkup(<PhotoEvidence claimId={claim.id} photos={claim.photos} readings={{}} />);
    const img = html.match(/<img[^>]*>/)![0];
    expect(img).toContain("aspect-[4/3]");
    expect(img).toContain("object-contain");
    expect(img).not.toContain("h-32");
    const missing = renderToStaticMarkup(<PhotoEvidence claimId={claim.id} photos={[{ ...claim.photos[0]!, url: "https://x.example.com/a.jpg" }]} readings={{}} />);
    expect(missing).toMatch(/aspect-\[4\/3\][^"]*"[^>]*>Photo unavailable|class="[^"]*aspect-\[4\/3\][^"]*">\s*Photo unavailable/);
  });
});

describe("decision reasons in words", () => {
  it("words the rules-only checker's duplicate reasons and keeps the claim id linkable", () => {
    expect(reasonText(`duplicate same_payout with claim ${OTHER} (payout destination)`)).toBe(`same payout destination as claim ${OTHER}`);
    expect(reasonText(`duplicate same_address with claim ${OTHER} (address)`)).toBe(`same home address as claim ${OTHER}`);
    expect(reasonText(`duplicate same_address with claim ${OTHER} (email)`)).toBe(`same email address as claim ${OTHER}`);
    expect(reasonText(`duplicate same_photo with claim ${OTHER} (label sha256)`)).toBe(`same label photo as claim ${OTHER} (identical file)`);
    expect(reasonText(`duplicate same_photo with claim ${OTHER} (receipt phash 3)`)).toBe(`same receipt photo as claim ${OTHER} (near match)`);
    expect(reasonText(`duplicate same_serial with claim ${OTHER} (serial)`)).toBe(`same serial number as claim ${OTHER}`);
    expect(reasonText(`duplicate same_payout with claim ${OTHER}`)).toBe(`same payout destination as claim ${OTHER}`);
    expect(reasonText(`duplicate same_device with claim ${OTHER} (fingerprint_x)`)).toBe(`possible duplicate (same device) of claim ${OTHER} (fingerprint x)`);
    expect(reasonParts(reasonText(`duplicate same_payout with claim ${OTHER} (payout destination)`))).toEqual([
      { text: "same payout destination as claim " },
      { claimId: OTHER },
    ]);
  });

  it("words verdict tokens and leaves plain reasons as they are", () => {
    expect(reasonText("retailer not_listed; proof of purchase needed")).toBe("retailer is not on the sold-at list; proof of purchase needed");
    expect(reasonText("retailer unknown; proof of purchase needed")).toBe("retailer unknown; proof of purchase needed");
    expect(reasonText("model not_provided")).toBe("model number not given");
    expect(reasonText("model ambiguous")).toBe("model number could match more than one rule");
    expect(reasonText("date code unparsed")).toBe("date code could not be read");
    expect(reasonText("date code not_provided")).toBe("date code not given");
    expect(reasonText("purchase date after")).toBe("bought after the recall window");
    expect(reasonText("every check passed")).toBe("every check passed");
    expect(reasonText("The label shows TL1302Z, a recalled model (T1).")).toBe("The label shows TL1302Z, a recalled model (T1).");
    expect(reasonText("serial out_of_range for R:serial")).toBe("serial out of range for R:serial");
  });

  it("shows no raw token in the panel, capitalizes each reason, and names the decider", () => {
    const decision = {
      ...claim.decision!,
      outcome: "flag" as const,
      reasons: [`duplicate same_payout with claim ${OTHER} (payout destination)`, "retailer not_listed; proof of purchase needed"],
      decided_by: "rules_only",
    };
    const html = renderToStaticMarkup(<DecisionPanel claimId={claim.id} decision={decision} decidedAt={claim.decided_at} />);
    expect(html).not.toMatch(/not_listed|same_payout|rules_only/);
    expect(html).toContain("same payout destination as claim ");
    expect(html).toContain(`href="/app/claims/${OTHER}"`);
    expect(html).toContain("retailer is not on the sold-at list");
    expect(html.match(/<li class="first-letter:uppercase" data-reason/g)).toHaveLength(2);
    expect(html).toContain("by the rules-only checker ·");
  });
});

describe("words for backend identifiers", () => {
  it("names graphs, deciders, proof kinds, and audit actions", () => {
    expect(graphLabel("rules_only")).toBe("Rules-only checker");
    expect(graphLabel("adjudicate")).toBe("Adjudication agent");
    expect(graphLabel("new_graph")).toBe("New graph");
    expect([decidedByLabel("agent"), decidedByLabel("rules_only"), decidedByLabel("user_2abc"), decidedByLabel(null)]).toEqual([
      "the adjudication agent",
      "the rules-only checker",
      "user_2abc",
      "an unknown decider",
    ]);
    expect(proofSentence(["destruction_photo", "receipt", "registration"])).toBe("Proof required: destruction photo, receipt, and registration.");
    expect(proofSentence(["receipt", "label_photo"])).toBe("Proof required: receipt and label photo.");
    expect(proofSentence(["none"])).toBe("No proof required.");
    expect(proofSentence([])).toBeNull();
    expect([auditActionLabel("claim_adjudicated"), auditActionLabel("claim_paid"), auditActionLabel("payout_hold_cleared")]).toEqual(["Adjudicated", "Paid", "Payout hold cleared"]);
    expect(humanize("")).toBe("");
  });

  it("shows the run's graph and cost in words on the claim page", () => {
    const html = withQuery(<ClaimDetailLive initialClaim={claim} initialTrace={null} loadedAt={Date.now()} />);
    expect(html).toContain("Rules-only checker · 7 steps · $0.00");
    expect(html).not.toContain("$0.000000");
    expect(html).not.toContain("rules_only ·");
  });

  it("words the audit trail: labelled actions in the table's size, and statuses as the badges say them", () => {
    const html = renderToStaticMarkup(
      <AuditTrail
        entries={[
          { action: "claim_adjudicated", actor: "system", at: "2026-10-21T20:55:00Z", before: { status: "submitted" }, after: { status: "needs_info" } },
          { action: "claim_paid", actor: "seed", at: "2026-10-21T20:56:00Z", before: { status: "approved" }, after: { status: "paid" } },
          { action: "claim_moved", actor: "system", at: "2026-10-21T20:57:00Z", before: null, after: { status: "archived_by_ops" } },
        ]}
      />,
    );
    expect(html).toContain('<td class="py-1 pr-3 text-xs" title="claim_adjudicated">Adjudicated</td>');
    expect(html).toContain(">Paid</td>");
    expect(html).toContain("Submitted → Needs info");
    expect(html).toContain("Approved → Paid");
    expect(html).toContain("→ Archived by ops");
    expect(html).not.toMatch(/>claim_adjudicated</);
  });
});

describe("KPI tiles", () => {
  it("pins each program value to the tile's foot and draws the attention accent without extra height", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={open} />);
    const tiles = html.match(/<div class="[^"]*" data-kpi="[^"]*">/g)!;
    expect(tiles).toHaveLength(6);
    for (const t of tiles) {
      expect(t).toContain("flex min-w-0 flex-col");
      expect(t).not.toContain("border-t-2");
    }
    expect(html).toContain("shadow-[inset_0_1px_0_0_var(--warn)]");
    expect(html.match(/kpi-value mt-auto break-words pt-2/g)).toHaveLength(6); // a value moves whole ("$100.00" split at 1280)
    expect(html).not.toMatch(/kpi-value[^"]*overflow-wrap:anywhere/);
    expect(html).toContain("xl:grid-cols-6");
  });

  it("does the same on the portfolio, and spans the first of five tiles so none is left alone in two columns", () => {
    const html = renderToStaticMarkup(<PortfolioSummary programs={programs} />);
    expect(html).not.toContain("border-t-2");
    expect(html.match(/kpi-value mt-auto pt-2/g)).toHaveLength(5);
    expect(html).toMatch(/class="[^"]*col-span-2 xl:col-span-1[^"]*" data-portfolio-stat="Open products"/);
    expect(html).toContain("xl:grid-cols-5");
  });
});

describe("program page", () => {
  it("prints every share of filed claims with one decimal", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={{ ...open, claim_counts: { paid: 110, approved: 0, denied: 390 } }} />);
    const share = (s: string) => html.split(`data-status-row="${s}"`)[1]!.match(/text-ink-muted">([^<]*)<\/td>/)![1];
    expect([share("paid"), share("approved"), share("denied")]).toEqual(["22.0%", "0.0%", "78.0%"]);
  });

  it("words the proof kinds", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={open} />);
    expect(html).toContain("Proof required: destruction photo.");
    expect(html).not.toContain("destruction_photo");
  });

  it("gives every header action one radius, with the claims queue as the primary", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={open} />);
    const header = html.slice(0, html.indexOf("<dl"));
    const links = header.match(/<a class="[^"]*"/g)!;
    expect(links).toHaveLength(5);
    for (const l of links) {
      expect(l).toContain("rounded-md");
      expect(l).not.toMatch(/\brounded /);
    }
    expect(header).toMatch(/<a class="[^"]*bg-accent[^"]*" href="[^"]*\/claims">Open claims queue</);
  });

  it("marks a destruction instruction that does not read as a sentence and links to the settings", () => {
    const cut = "com/3M/en_US/scotch-brand/tl1302Z-laminator-recall/, confirm that their laminator is included in the recall";
    const html = renderToStaticMarkup(<ProgramOverview program={{ ...open, remedy_rules: { ...open.remedy_rules, destruction_instruction: cut } }} />);
    expect(html).toContain(cut);
    expect(html).toContain("data-instruction-cut");
    expect(html).toContain("This looks cut off from the notice.");
    expect(html).toContain(`href="/app/programs/${open.id}/settings">Rules and settings</a>`);
    expect(renderToStaticMarkup(<ProgramOverview program={open} />)).not.toContain("data-instruction-cut");
    expect(instructionLooksCut("Cut the power cord and write RECALLED on the laminator")).toBe(false);
    expect(instructionLooksCut("cut the cord")).toBe(false); // the portal's own test: a lowercase instruction reads fine
    expect(instructionLooksCut("com/3M/en_US/scotch-brand/tl1302Z-laminator-recall/, confirm that their laminator is included")).toBe(true);
    expect(instructionLooksCut("Visit www.example.com and cut the cord")).toBe(true);
    expect(instructionLooksCut("Cut the cord, see example.com/recall")).toBe(true);
  });

  it("links an https registration address and leaves anything else as text", () => {
    const url = "https://www.scotchbrand.com/3M/en_US/scotch-brand/tl1302Z-laminator-recall/";
    const html = renderToStaticMarkup(<ProgramOverview program={{ ...open, remedy_rules: { ...open.remedy_rules, registration_url: url } }} />);
    expect(html).toContain(`<a href="${url}" target="_blank" rel="noopener noreferrer"`);
    expect(registrationHref("www.example.com/recall")).toBeNull();
    expect(registrationHref("http://example.com/recall")).toBeNull();
    expect(registrationHref("javascript:alert(1)")).toBeNull();
    expect(registrationHref(null)).toBeNull();
  });
});

describe("trace page", () => {
  const html = renderToStaticMarkup(<TraceView trace={trace} claimId={claim.id} cited={[]} />);

  it("formats costs and counts steps, not rows", () => {
    expect(html).toContain("9 steps · $0.0031 charged");
    expect(html).not.toContain(" rows ");
    expect(html).not.toMatch(/\$0\.000\d{3}/);
    expect(html).toContain("1800 ms · 2100/120 tokens · $0.0003"); // was $0.000270
  });

  it("says which steps the step cap counts, and keeps the cost's unit in its label", () => {
    expect(html).toContain('data-cap="Steps"');
    expect(html).toContain("Steps counted toward the cap");
    expect(html).toMatch(/data-cap="Cost \(USD\)"[\s\S]*?0\.0031 <span[^>]*>of 0\.02</);
  });

  it("tells a draft claim's reviewer that nothing has been submitted yet", () => {
    const draft = withQuery(<TraceLive claimId={claim.id} initialTrace={null} cited={[]} loadedAt={Date.now()} claimStatus="draft" />);
    expect(draft).toContain("Not submitted yet");
    expect(draft).toContain("The claimant has not submitted this claim yet, so nothing has been checked.");
    expect(draft).not.toContain("The worker picks up");
    const submitted = withQuery(<TraceLive claimId={claim.id} initialTrace={null} cited={[]} loadedAt={Date.now()} claimStatus="submitted" />);
    expect(submitted).toContain("The worker picks up submitted claims");
  });

  it("says the same on the claim page's latest run", () => {
    const html2 = withQuery(<ClaimDetailLive initialClaim={{ ...claim, status: "draft", run: null, decision: null }} initialTrace={null} loadedAt={Date.now()} />);
    expect(html2).toContain("Not submitted yet, so nothing has been checked.");
  });
});

describe("claims queue at laptop widths", () => {
  const html = renderToStaticMarkup(<ClaimsTable claims={page.items} />);

  it("shows cards below lg and holds Retailer and Decided for xl", () => {
    expect(html).toContain('<ul class="grid gap-2 lg:hidden"');
    expect(html).toMatch(/class="hidden overflow-x-auto [^"]*lg:block"/);
    const head = html.slice(html.indexOf("<thead"), html.indexOf("</thead>"));
    expect(head).toMatch(/xl:table-cell">Retailer</);
    expect(head).toMatch(/xl:table-cell">Decided</);
    for (const col of ["Model", "Refund", "Created"]) expect(head).not.toMatch(new RegExp(`hidden[^"]*">${col}<`));
  });

  it("stacks Created's date over its time at every width (on one line the table overflowed at 1280 to 1440 px)", () => {
    expect(html).toContain('<span class="block">Oct 16, 2026</span><span class="text-ink-muted">22:40 UTC</span>');
  });
});

describe("sentence case", () => {
  it("titles a filtered empty queue in sentence case and points back to All", () => {
    expect(emptyQueueTitle("capped")).toBe("No capped claims");
    expect(emptyQueueTitle("needs_info")).toBe("No claims waiting on the claimant");
    expect(emptyQueueTitle("draft")).toBe("No unsubmitted claims");
    expect(emptyQueueTitle(null)).toBe("No claims yet");
    const html = withQuery(
      <ClaimsQueue programId={open.id} params={{ status: "capped", cursor: null }} initialPage={{ ok: true, data: { items: [], next_cursor: null } }} initialProgram={open} loadedAt={Date.now()} />,
    );
    expect(html).toContain("No capped claims");
    expect(html).toContain("Choose All to see every submitted claim.");
    expect(html).not.toContain("with status Capped");
  });

  it("starts the claim header's date with a capital, and gives its links one style", () => {
    const html = withQuery(<ClaimDetailLive initialClaim={claim} initialTrace={null} loadedAt={Date.now()} />);
    expect(html).toContain(">Created Oct 16, 2026");
    expect(html).not.toContain(">created ");
    const trace = html.match(/<a class="([^"]*)" href="\/app\/claims\/[^"]*\/trace">Open trace</)![1];
    const queue = html.match(/<a class="([^"]*)" href="\/app\/programs\/[^"]*\/claims">Claims queue</)![1];
    expect(trace).toBe(queue);
    expect(trace).toContain("rounded-md");
  });
});

