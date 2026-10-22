import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ProgramOverview } from "@/components/ProgramOverview";
import { ProgramsTable } from "@/components/ProgramsTable";
import { ProgramList } from "@/lib/api-types";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

const list = ProgramList.parse(JSON.parse(readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8")));
const [open, draft] = list.items as [(typeof list.items)[number], (typeof list.items)[number]];

describe("ProgramsTable", () => {
  const html = renderToStaticMarkup(<ProgramsTable programs={list.items} />);

  it("links each program to its overview", () => {
    expect(html).toContain('href="/app/programs/0874f66f-bfb5-552b-bd93-6266f49c1d5a"');
    expect(html).toContain("Scotch Thermal Laminators");
  });

  it("shows the derived figures for an open program", () => {
    expect(html).toContain("53,634");
    expect(html).toContain(">500<"); // filed: everything except the 3 issued links
    expect(html).toContain(">117<"); // awaiting review: flagged
    expect(html).toContain("110");
    expect(html).toContain("(0.2%)");
    expect(html).toContain("Oct 16, 2026");
  });

  it("handles a draft with no units and no claims", () => {
    expect(html).toContain("Not opened");
    expect(html).toContain('data-status="draft"');
    expect(html).toContain("(—)");
  });

  it("escapes program titles", () => {
    expect(html).toContain("&lt;b&gt;draft&lt;/b&gt;");
    expect(html).not.toContain("<b>draft</b>");
  });

  it("is a labelled table with column headers", () => {
    expect(html).toContain('<caption class="sr-only">Recall programs</caption>');
    expect(html.match(/scope="col"/g)).toHaveLength(7);
  });
});

describe("ProgramOverview", () => {
  it("renders the summary, every claim status, rules and remedy", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={open} />);
    expect(html).toContain("<h1");
    expect(html).toContain("$100.00"); // value threshold
    for (const s of ["draft", "submitted", "needs_info", "flagged", "under_review", "capped", "approved", "denied", "paid", "closed"]) {
      expect(html).toContain(`data-status-row="${s}"`);
    }
    expect(html).toContain("TL1302Z");
    expect(html).toContain("Amazon.com");
    expect(html).toContain("Jun 1, 2024 to Sep 30, 2026");
    expect(html).toContain("$44.00 to $56.00");
    expect(html).toContain("2501 through 2534");
    expect(html).toContain("Refund");
    expect(html).toContain("Upload a photo of the cut cord");
    expect(html).toContain("Proof required: destruction photo");
    expect(html).toContain("Cut the power cord");
  });

  it("does not turn the notice's registration URL into a link", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={open} />);
    expect(html).toContain("www.example.com/recall");
    expect(html).not.toContain('href="www.example.com/recall"');
  });

  it("says what the notice does not state", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={draft} />);
    expect(html).toContain("Not stated in the notice");
    expect(html.match(/Not stated/g)!.length).toBeGreaterThanOrEqual(8);
    expect(html).toContain("not opened");
    expect(html).not.toContain("Options");
  });

  it("shows a status the backend added later without failing", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={{ ...open, claim_counts: { archived: 2 } }} />);
    expect(html).toContain('data-status-row="archived"');
    expect(html).toContain("In progress");
  });
});
