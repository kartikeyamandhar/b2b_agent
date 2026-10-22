// Phase E: owner registrations, matching, drafts, and the simulated send.
import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import CampaignPage from "@/app/app/programs/[id]/campaign/page";
import { Campaign, CampaignStatus, SampleNotices } from "@/components/Campaign";
import { CampaignSummary } from "@/lib/api-types";
import { campaignStage, checkCsvFile, notificationRows, rejectedRows, sampleDrafts, sendTotal, splitPlaceholder, templateCsv } from "@/lib/campaign";

const P = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";
const empty = CampaignSummary.parse({ registrations: 0, matched: 0, unmatched: 0, claim_link: null, notifications: {}, sample: [], simulated: true });
const drafted = CampaignSummary.parse({
  registrations: 3,
  matched: 2,
  unmatched: 1,
  claim_link: null, // not taking claims: the API leaves the placeholder in the sample
  notifications: { "email:draft": 2, "phone_text:sent": 1, "fax:draft": 4 },
  sample: [{ channel: "email", status: "draft", subject: "Safety recall: Laminators", body: "Dear Sam,\n\nFile your claim at {claim_link}. <b>x</b>" }],
  simulated: true,
});
const program = (JSON.parse(readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8")) as { items: unknown[] }).items[0];

beforeEach(() => {
  vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
  vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("campaign model", () => {
  it("writes a template the API accepts: its columns and synthetic owners only", () => {
    const csv = templateCsv("TL1302Z");
    const [header, ...rows] = csv.trim().split("\n");
    expect(header).toBe("external_ref,name,email,phone,address,model,serial,date_code,purchase_date");
    expect(rows).toHaveLength(2);
    expect(csv).toContain("@example.com");
    expect(csv).toMatch(/555-01\d\d/);
    expect(csv).toContain('"TL1302Z"');
    expect(templateCsv('=HYPERLINK("x")')).toContain(`"'=HYPERLINK(""x"")"`);
  });

  it("checks the file before uploading", () => {
    expect(checkCsvFile(null)).toMatchObject({ ok: false });
    expect(checkCsvFile({ name: "owners.xlsx", size: 10, type: "application/vnd.ms-excel" })).toMatchObject({ ok: false });
    expect(checkCsvFile({ name: "owners.csv", size: 0, type: "text/csv" })).toMatchObject({ ok: false, reason: "The file is empty." });
    expect(checkCsvFile({ name: "owners.csv", size: 5_000_001, type: "text/csv" })).toMatchObject({ ok: false });
    expect(checkCsvFile({ name: "OWNERS.CSV", size: 1200, type: "" })).toEqual({ ok: true });
  });

  it("reads rejected rows defensively", () => {
    expect(rejectedRows([{ row: 3, reason: "email is not synthetic" }, { reason: 5 }])).toEqual([
      { row: 3, reason: "email is not synthetic" },
      { row: null, reason: "rejected" },
    ]);
  });

  it("tabulates notices per channel, keeping unknown channels", () => {
    const rows = notificationRows(drafted.notifications);
    expect(rows.map((r) => r.channel)).toEqual(["email", "mail", "phone_text", "fax"]);
    expect(rows[0]).toEqual({ channel: "email", draft: 2, sent: 0, other: 0 });
    expect(rows[2]).toMatchObject({ sent: 1 });
    expect(sendTotal(rows, null)).toBe(6);
    expect(sendTotal(rows, "email")).toBe(2);
  });

  it("knows the next step", () => {
    expect(campaignStage(empty)).toBe("upload");
    expect(campaignStage({ ...empty, registrations: 4 })).toBe("match");
    expect(campaignStage({ ...empty, registrations: 4, matched: 0, unmatched: 4 })).toBe("match");
    expect(campaignStage({ ...empty, registrations: 4, matched: 3, unmatched: 1 })).toBe("draft");
    expect(campaignStage(drafted)).toBe("send");
    expect(campaignStage({ ...drafted, notifications: { "email:sent": 2 } })).toBe("done");
  });

  it("never turns the claim link placeholder into a link", () => {
    expect(splitPlaceholder("Go to {claim_link} now")).toEqual([
      { text: "Go to ", link: false },
      { text: "{claim_link}", link: true },
      { text: " now", link: false },
    ]);
    expect(sampleDrafts([{ channel: 1 }])).toEqual([{ channel: "", status: "", subject: "", body: "" }]);
  });
});

describe("components", () => {
  it("shows counts and the channel table to everyone", () => {
    const html = renderToStaticMarkup(<CampaignStatus summary={drafted} />);
    expect(html).toContain('data-channel-row="email"');
    expect(html).toContain("Text message");
  });

  it("shows samples as text, with the placeholder marked and markup escaped", () => {
    const html = renderToStaticMarkup(<SampleNotices sample={drafted.sample} />);
    expect(html).toContain("data-link-placeholder");
    expect(html).not.toContain("{claim_link}");
    expect(html).toContain("&lt;b&gt;x&lt;/b&gt;");
    expect(html).not.toContain("<a ");
  });

  it("gives an admin the four steps, says sending is simulated, and asks before marking sent", () => {
    const html = renderToStaticMarkup(<Campaign programId={P} programStatus="open" initial={drafted} canAdmin />);
    for (const s of ["upload", "match", "draft", "send"]) expect(html).toContain(`data-step="${s}"`);
    expect(html).toContain("Sending is simulated");
    expect(html).toContain("data-send-start");
    expect(html).not.toContain("data-send-confirm");
    expect(html).toMatch(/data-step="send"[^>]*data-current="true"|data-current="true"[^>]*data-step="send"/);
  });

  it("blocks drafting until the product is open", () => {
    const html = renderToStaticMarkup(<Campaign programId={P} programStatus="draft" initial={{ ...drafted, notifications: {} }} canAdmin />);
    expect(html).toContain("Open the product before drafting notices");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*data-draft/);
  });

  it("is read-only for reviewers", () => {
    const html = renderToStaticMarkup(<Campaign programId={P} programStatus="open" initial={drafted} canAdmin={false} />);
    expect(html).toContain("This account can read the campaign but not upload registrations");
    expect(html).not.toContain("data-upload");
    expect(html).not.toContain("data-send-start");
  });
});

describe("/app/programs/[id]/campaign", () => {
  it("reads the program and the campaign summary, and nothing else", async () => {
    const calls: { url: string; method: string }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push({ url, method: init.method ?? "GET" });
      return url.endsWith("/campaign") ? Response.json(empty) : Response.json(program);
    });
    const html = renderToStaticMarkup(await CampaignPage({ params: Promise.resolve({ id: P }) }));
    expect(calls.map((c) => c.url).sort()).toEqual([`http://127.0.0.1:8000/v1/programs/${P}`, `http://127.0.0.1:8000/v1/programs/${P}/campaign`]);
    expect(calls.every((c) => c.method === "GET")).toBe(true);
    expect(html).toContain("Owner notices");
    expect(html).toMatch(/data-step="upload"[^>]*data-current="true"/);
  });
});
