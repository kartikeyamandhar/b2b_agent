// Phase A, as of plan v3: editing a product's rules and threshold, and its lifecycle (draft → open → closed). Products
// are no longer created in the console (the operator's approval creates and opens them); the recall search and the
// create body stay as pure functions.
import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import NewProgramPage from "@/app/app/programs/new/page";
import ProgramSettingsPage from "@/app/app/programs/[id]/settings/page";
import ProgramPage from "@/app/app/programs/[id]/page";
import ProgramsPage from "@/app/app/programs/page";
import { ProgramSettings } from "@/components/ProgramSettings";
import { Program, ProgramPatch, RecallSummary } from "@/lib/api-types";
import { recallSearch } from "@/lib/program-create";
import { draftChanged, draftFromProgram, lifecycle, parseDollars, rulesPatch, rulesWarnings } from "@/lib/program-rules";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8")) as { items: unknown[] };
const open = Program.parse(fixture.items[0]);
const draft = Program.parse(fixture.items[1]);
const closed = Program.parse({ ...(fixture.items[0] as object), status: "closed", closed_at: "2026-10-20T00:00:00Z" });
const recall = RecallSummary.parse({
  id: "01cde478-cc0e-4df1-b8b5-c5900bc79bc1",
  source: "cpsc",
  source_id: "27013",
  recall_date: "2026-10-01",
  title: "Golden Resale Recalls Portable Camping Showers",
  url: "https://www.cpsc.gov/Recalls/2027/Golden-Resale",
  units_affected: 400,
  units_status: "ok",
  category: "Shower Items",
});

type Call = { url: string; method: string; headers: Headers; body: string | null };
let calls: Call[];
function serve(respond: (url: string, method: string) => Response) {
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const method = init.method ?? "GET";
    calls.push({ url, method, headers: new Headers(init.headers), body: typeof init.body === "string" ? init.body : null });
    return respond(url, method);
  });
}

beforeEach(() => {
  calls = [];
  vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
  vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("rule editor model", () => {
  it("round-trips a program without changing anything", () => {
    const d = draftFromProgram(open);
    expect(d.models).toBe("TL1302Z\nTL901X");
    expect(d.priceMin).toBe("44.00");
    expect(d.threshold).toBe("100.00");
    const r = rulesPatch(d, open);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.patch.identification_rules).toEqual(open.identification_rules);
    expect(r.patch.remedy_rules).toEqual(open.remedy_rules);
    expect(r.patch.value_threshold_cents).toBe(10000);
    expect(r.patch.status).toBeUndefined();
  });

  it("keeps fields the editor does not show: evidence, options, units, and keys a newer contract adds", () => {
    const withExtras = Program.parse({
      ...(fixture.items[0] as object),
      identification_rules: { ...open.identification_rules, evidence: [{ field: "models", quote: "TL1302Z", start: 3, end: 10 }], future_field: [1] },
    });
    const r = rulesPatch({ ...draftFromProgram(withExtras), models: "TL1302Z\n  NEW-1 \nTL1302Z\n\n" }, withExtras);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.patch.identification_rules?.models).toEqual(["TL1302Z", "NEW-1"]);
    expect(r.patch.identification_rules?.evidence).toHaveLength(1);
    expect(r.patch.identification_rules?.units_affected).toBe(53634);
    expect((r.patch.identification_rules as Record<string, unknown>).future_field).toEqual([1]);
    expect(r.patch.remedy_rules?.options).toEqual(open.remedy_rules.options);
    expect(ProgramPatch.safeParse(r.patch).success).toBe(true);
  });

  it("turns empty fields into nulls and empty lists, never empty strings", () => {
    // No remedy options: with options naming a remedy, an unset remedy is refused (claims would keep using theirs).
    const noOptions = { ...open, remedy_rules: { ...open.remedy_rules, options: [] } };
    const d = { ...draftFromProgram(noOptions), modelLabelLocation: "  ", priceMin: "", priceMax: "", soldFrom: "", soldTo: "", remedyType: "", contact: "", serialRanges: [{ raw: "", prefix: "", start: "", end: "" }] };
    const r = rulesPatch(d, noOptions);
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    expect(r.patch.identification_rules).toMatchObject({ model_label_location: null, price_range_cents: null, sold_from: null, serial_ranges: [] });
    expect(r.patch.remedy_rules).toMatchObject({ remedy_type: null, contact: null });
  });

  it("refuses values the API would refuse, field by field", () => {
    const base = draftFromProgram(open);
    const bad = rulesPatch(
      {
        ...base,
        soldFrom: "2026-02-30",
        priceMin: "abc",
        threshold: "-5",
        serialRanges: [{ raw: "", prefix: "A", start: "1", end: "9" }],
        dateCodes: [{ raw: "2501", format: "", start: "", end: "" }],
        registrationUrl: "javascript:alert(1)",
      },
      open,
    );
    expect(bad.ok).toBe(false);
    if (bad.ok) return;
    expect(Object.keys(bad.errors).sort()).toEqual(["dateCodes", "priceMin", "registrationUrl", "serialRanges", "soldFrom", "threshold"]);
    const order = rulesPatch({ ...base, soldFrom: "2026-05-01", soldTo: "2026-01-01", priceMin: "60", priceMax: "50" }, open);
    expect(order.ok ? {} : order.errors).toMatchObject({ soldTo: expect.any(String), priceMax: expect.any(String) });
  });

  it("reads dollar amounts the way people type them", () => {
    expect(parseDollars("100")).toBe(10000);
    expect(parseDollars("$1,250.5")).toBe(125050);
    expect(parseDollars(" 0.99 ")).toBe(99);
    for (const v of ["", "-1", "1.234", "ten", "1e3"]) expect(parseDollars(v)).toBeNull();
  });

  it("notices unsaved edits", () => {
    const d = draftFromProgram(open);
    expect(draftChanged(d, draftFromProgram(open))).toBe(false);
    expect(draftChanged({ ...d, contact: "x" }, d)).toBe(true);
  });

  it("allows only draft → open → closed", () => {
    expect(lifecycle("draft")).toEqual({ next: "open", editable: true });
    expect(lifecycle("open")).toEqual({ next: "closed", editable: true });
    expect(lifecycle("closed")).toEqual({ next: null, editable: false });
    expect(lifecycle("archived").next).toBeNull();
  });

  it("warns when the rules cannot identify a product", () => {
    expect(rulesWarnings(draft)).toHaveLength(2);
    expect(rulesWarnings(open)).toEqual([]);
  });
});

describe("recall search", () => {
  it("builds the recall search query", () => {
    expect(recallSearch("  laminator ", "")).toBe("?q=laminator&limit=25");
    expect(recallSearch("", "2026-09-01", 500)).toBe("?since=2026-09-01&limit=100");
    expect(recallSearch("a&b=c", "not-a-date")).toBe("?q=a%26b%3Dc&limit=25");
  });
});

describe("ProgramSettings", () => {
  it("lets an admin edit a draft and open it", () => {
    const html = renderToStaticMarkup(<ProgramSettings program={draft} canAdmin />);
    expect(html).not.toContain("data-created");
    expect(html).toContain('data-lifecycle="open"');
    expect(html).toContain("Open the product");
    expect(html).toContain("Free covers one.");
    expect(html).toContain("data-save-rules");
    expect(html).toContain("No model numbers, serial ranges, or date codes were found");
    expect(html).not.toMatch(/<textarea[^>]*disabled=""/);
  });

  it("warns that closing is permanent and that edits apply only to later decisions", () => {
    const html = renderToStaticMarkup(<ProgramSettings program={open} canAdmin />);
    expect(html).toContain('data-lifecycle="closed"');
    expect(html).toContain("Closing is permanent");
    expect(html).toContain("Changes apply to claims decided after you save");
    expect(html).toContain("TL1302Z\nTL901X</textarea>");
    expect(html).toContain("paid by check");
  });

  it("is read-only for a reviewer, with no lifecycle control", () => {
    const html = renderToStaticMarkup(<ProgramSettings program={open} canAdmin={false} />);
    expect(html).toContain("This account can read these settings but not change them.");
    expect(html).not.toMatch(/\b(admin|members?|team|seats?)\b/i);
    expect(html).not.toContain("data-save-rules");
    expect(html).not.toContain("data-lifecycle");
    expect(html).toMatch(/<textarea[^>]*disabled=""/);
  });

  it("freezes a closed program", () => {
    const html = renderToStaticMarkup(<ProgramSettings program={closed} canAdmin />);
    expect(html).not.toContain("data-save-rules");
    expect(html).toContain("This product is closed");
    expect(html).toMatch(/<input[^>]*disabled=""/);
  });
});

describe("pages", () => {
  const programs = () => Response.json(fixture);

  it("never offers to create a product, and points to Recall access for another recall", async () => {
    serve(programs);
    const html = renderToStaticMarkup(await ProgramsPage());
    expect(html).not.toContain('href="/app/programs/new"');
    expect(html).not.toContain("New program");
    expect(html).toContain('href="/app/access"');
  });

  it("explains the empty list: ask under Recall access, and the operator's approval opens the product", async () => {
    serve(() => Response.json({ items: [] }));
    const html = renderToStaticMarkup(await ProgramsPage());
    expect(html).toContain("No products yet");
    expect(html).toContain('Ask for a recall under <a href="/app/access">Recall access</a>; when the operator approves it, your product opens here with its claim');
    expect(html).not.toContain("Create the first program");
  });

  it("sends the retired new-program page to Recall access", () => {
    let thrown: unknown = null;
    try {
      NewProgramPage();
    } catch (err) {
      thrown = err;
    }
    expect(String((thrown as { digest?: unknown } | null)?.digest)).toContain("/app/access");
  });

  it("loads settings for the product", async () => {
    serve(() => Response.json(fixture.items[1]));
    const html = renderToStaticMarkup(await ProgramSettingsPage({ params: Promise.resolve({ id: draft.id }) }));
    expect(calls[0]).toMatchObject({ url: `http://127.0.0.1:8000/v1/programs/${draft.id}`, method: "GET" });
    expect(html).toContain("Rules and settings");
    expect(html).toContain("← Products");
    expect(html).not.toContain("<b>draft</b>");
  });

  it("shows a draft banner with the way to open it on the overview", async () => {
    serve(() => Response.json(fixture.items[1]));
    const html = renderToStaticMarkup(await ProgramPage({ params: Promise.resolve({ id: draft.id }) }));
    expect(html).toContain("data-draft-banner");
    expect(html).toContain(`href="/app/programs/${draft.id}/settings"`);
  });
});

describe("rules evidence (02-ui.md RulesEditor: quotes beside each value, verbatim mark)", () => {
  const withEvidence = Program.parse({
    ...(fixture.items[0] as object),
    identification_rules: {
      ...open.identification_rules,
      evidence: [
        { field: "sold_at", quote: "Walmart", start: 90, end: 97 },
        { field: "identification.models", quote: "TL1302Z", start: 10, end: 17 },
        { field: "sold_at", quote: "Amazon.com", start: 50, end: 60 },
        { field: "sold_at", quote: "Amazon.com", start: 120, end: 130 },
        { field: "sold_to", quote: "through September 2026", start: 200, end: 222 },
      ],
    },
    remedy_rules: { ...open.remedy_rules, evidence: [{ field: "contact", quote: "800-555-0100", start: 300, end: 312 }] },
  });

  it("groups quotes by editor field in notice order, accepting path-qualified names", async () => {
    const { evidenceByField } = await import("@/lib/program-rules");
    expect(evidenceByField(withEvidence)).toEqual({
      models: ["TL1302Z"],
      soldAt: ["Amazon.com", "Walmart"],
      soldFrom: ["through September 2026"],
      contact: ["800-555-0100"],
    });
    expect(evidenceByField(draft)).toEqual({});
  });

  it("shows a verbatim mark beside values backed by the notice", () => {
    const html = renderToStaticMarkup(<ProgramSettings program={withEvidence} canAdmin />);
    expect(html.match(/data-evidence=/g)?.length).toBe(4);
    expect(html).toContain("Verbatim in the notice:");
    expect(html).toContain("“TL1302Z”");
    expect(html).not.toContain("data-evidence-edited");
  });

  it("says when an edit leaves the quote behind", async () => {
    const { Evidence } = await import("@/components/ProgramSettings");
    const html = renderToStaticMarkup(<Evidence quotes={["TL1302Z"]} edited />);
    expect(html).toContain("data-evidence-edited");
    expect(html).toContain("Previously in the notice:");
    expect(renderToStaticMarkup(<Evidence quotes={[]} edited={false} />)).toBe("");
  });
});
