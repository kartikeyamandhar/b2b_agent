// Plan v3 on the company's product pages (owner decision 2026-10-22): one public claim link per product with its QR
// code and a one-page poster, the paused and refund-amount states, the "check the rules" note, the products list
// without "New program", drafts out of the default queue, the campaign's product link, private links sent only to
// the claim's email, and refunds the claimant always chooses.
import { readFileSync, readdirSync } from "node:fs";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import ProgramPage from "@/app/app/programs/[id]/page";
import ProgramsPage from "@/app/app/programs/page";
import { CampaignStatus, SampleNotices } from "@/components/Campaign";
import { ProductLinkCard, REPLACE_WARNING, ReplaceLinkConfirm, replaceIntakeLink } from "@/components/ProductLinkCard";
import { PayoutPanel } from "@/components/ClaimPanels";
import { ClaimsQueue, emptyQueueTitle } from "@/components/ClaimsQueue";
import { ProgramCards } from "@/components/ProgramPortfolio";
import { ProductBanner, ProgramOverview } from "@/components/ProgramOverview";
import { ProgramSettings } from "@/components/ProgramSettings";
import { QrPanel, openPrintable } from "@/components/QrCode";
import { StatusFilter } from "@/components/QueueControls";
import { ReissuedLink, sendOnlyTo } from "@/components/ReissueLink";
import { RulesCheckNote, RulesCheckReminder } from "@/components/RulesCheckNote";
import { CampaignSummary, Program, ProgramList, type PayoutView } from "@/lib/api-types";
import { claimsApiPath } from "@/lib/claims";
import { MISSING_REFUND_AMOUNT_SENTENCE, PLAN_FULL_SENTENCE, lifecycleProblemSentence, rulesWarnings } from "@/lib/program-rules";
import {
  RULES_NOTE_PREFIX,
  browserStore,
  dismissRulesNote,
  intakeReplacePath,
  linkState,
  posterFor,
  productNote,
  qrFileName,
  rulesNoteDismissed,
  type NoteStore,
} from "@/lib/products";
import { POSTER_INSTRUCTIONS, qrMatrix, qrPixels, qrPosterHtml } from "@/lib/qr";

const fixture = ProgramList.parse(JSON.parse(readFileSync(new URL("./fixtures/programs.json", import.meta.url), "utf8")));
const base = fixture.items[0]!;
const INTAKE = `https://recallops-five.vercel.app/intake/${"Q".repeat(60)}-_${"z".repeat(29)}`; // 91 characters, like a real token
const live = Program.parse({ ...base, intake_url: INTAKE, takes_new_claims: true, needs_refund_amount: false });
const paused = Program.parse({ ...base, intake_url: null, takes_new_claims: false, needs_refund_amount: false });
const needsAmount = Program.parse({
  ...base,
  status: "draft",
  opened_at: null,
  intake_url: null,
  takes_new_claims: false,
  needs_refund_amount: true,
  identification_rules: { ...base.identification_rules, price_range_cents: null },
});
const draft = Program.parse({ ...base, status: "draft", opened_at: null, intake_url: null, takes_new_claims: false, needs_refund_amount: false });
const closed = Program.parse({ ...base, status: "closed", closed_at: "2026-10-20T00:00:00Z", intake_url: null, takes_new_claims: false });

const withQuery = (el: ReactElement) => renderToStaticMarkup(<QueryClientProvider client={new QueryClient()}>{el}</QueryClientProvider>);

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("where the claim link stands", () => {
  it("shows a link only while the product takes new claims", () => {
    expect(linkState(live)).toEqual({ kind: "live", url: INTAKE });
    expect(linkState(paused)).toEqual({ kind: "paused" });
    expect(linkState(needsAmount)).toEqual({ kind: "needs_amount" });
    expect(linkState(draft)).toEqual({ kind: "draft" });
    expect(linkState(closed)).toEqual({ kind: "closed" });
    // An open product with no link and no answer about new claims (an older API) shows none.
    expect(linkState(base)).toEqual({ kind: "none" });
    expect(linkState({ ...live, status: "archived" })).toEqual({ kind: "none" });
  });

  it("marks paused and price-less products in the list, and nothing else", () => {
    expect(productNote(paused)).toBe("Paused: takes no new claims");
    expect(productNote(needsAmount)).toBe("Set the refund amount");
    for (const p of [live, draft, closed, base]) expect(productNote(p)).toBeNull();
  });

  it("replaces the link only for an id that can name a product", () => {
    expect(intakeReplacePath(base.id)).toBe(`/v1/programs/${base.id}/intake/replace`);
    expect(intakeReplacePath("../billing")).toBeNull();
  });
});

describe("the poster", () => {
  it("takes the company, the product, and its first distinct models", () => {
    const models = ["A1", " A1 ", "B2", "", ...Array.from({ length: 10 }, (_, i) => `M${i}`)];
    const p = posterFor({ title: "Acme Kettle", identification_rules: { models } }, INTAKE, "  Acme Inc. ");
    expect(p.company).toBe("Acme Inc.");
    expect(p.models).toEqual(["A1", "B2", "M0", "M1", "M2", "M3", "M4", "M5"]);
    expect(p.moreModels).toBe(4);
    expect(posterFor(base, INTAKE, " ").company).toBeNull();
    expect(posterFor(base, INTAKE, null).models).toEqual(["TL1302Z", "TL901X"]);
  });

  it("is one page: company and product, Scan to file a claim, one code, the link, the models, two lines", () => {
    const html = qrPosterHtml(posterFor(base, INTAKE, "Acme <Inc>"));
    expect(html.match(/<svg /g)).toHaveLength(1);
    expect(html).toContain('<p class="company">Acme &lt;Inc&gt;</p>');
    expect(html).toContain("<h1>Scotch Thermal Laminators</h1>");
    expect(html).toContain('<p class="scan">Scan to file a claim</p>');
    expect(html).toContain(`<p class="url">${INTAKE}</p>`);
    expect(html).toContain('<span class="label">Models</span> TL1302Z, TL901X</p>');
    for (const line of POSTER_INSTRUCTIONS) expect(html).toContain(`<span>${line.replace(/'/g, "&#39;")}</span>`);
    expect(POSTER_INSTRUCTIONS).toHaveLength(2);
    expect(html).not.toContain("<script");
    expect(html).toContain("page-break-inside:avoid");
  });

  it("hides everything but the poster when printed", () => {
    const html = qrPosterHtml(posterFor(base, INTAKE, null));
    expect(html).toContain("@media print{body{padding:0}.screen-note{display:none}}");
    expect(html).toContain('<p class="screen-note">');
    expect(html).not.toContain('class="company"');
  });

  it("escapes the product and counts the models it leaves off", () => {
    const html = qrPosterHtml({ company: null, title: '<img src=x onerror="1">', url: INTAKE, models: ["X<1>"], moreModels: 3 });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img src=x onerror=&quot;1&quot;&gt;");
    expect(html).toContain("X&lt;1&gt; and 3 more");
    expect(html).toContain("<title>Claim poster: &lt;img");
  });

  it("refuses a link no phone could read", () => {
    expect(() => qrPosterHtml({ company: null, title: "x", url: "https://example.com/é", models: [], moreModels: 0 })).toThrow();
  });

  it("opens in a new window with no handle back, and says when pop-ups are blocked", () => {
    const written: string[] = [];
    const fake = { opener: {} as unknown, document: { open: vi.fn(), write: (h: string) => written.push(h), close: vi.fn() }, focus: vi.fn(), print: vi.fn() };
    expect(openPrintable("<p>poster</p>", () => fake as unknown as Window)).toBe(true);
    expect(fake.opener).toBeNull();
    expect(fake.print).toHaveBeenCalledOnce();
    expect(written).toEqual(["<p>poster</p>"]);
    expect(openPrintable("<p>poster</p>", () => null)).toBe(false);
  });

  it("names the downloads after the product", () => {
    expect(qrFileName("Scotch Thermal Laminators!", base.id)).toBe("scotch-thermal-laminators-claim-link-qr");
    expect(qrFileName("<<>>", base.id)).toBe("product-0874f66f-claim-link-qr");
    const panel = renderToStaticMarkup(<QrPanel url={INTAKE} fileName="x-claim-link-qr" label="QR code for the claim link of Kettle" />);
    expect(panel).toContain('aria-label="QR code for the claim link of Kettle"');
    expect(panel).toContain("Download SVG");
    expect(panel).toContain("Download PNG");
  });
});

describe("the product's claim link card", () => {
  it("shows the link, its QR code, Copy, the poster, and Replace for an admin", () => {
    const html = renderToStaticMarkup(<ProductLinkCard program={live} url={INTAKE} canAdmin />);
    expect(html).toContain(`data-intake-url="true">${INTAKE}</code>`);
    expect(html).toContain(`width="${qrPixels(qrMatrix(INTAKE), 168)}"`);
    expect(html).toContain("data-copy-link");
    expect(html).toContain(">Print a poster<");
    expect(html).toContain("data-replace-start");
    expect(html).not.toContain("data-replace-confirm"); // nothing is replaced without the confirmation
    expect(html).toContain("Each claimant gets their own private link when they start a claim.");
  });

  it("offers no Replace without the admin role", () => {
    expect(renderToStaticMarkup(<ProductLinkCard program={live} url={INTAKE} />)).not.toContain("data-replace-start");
  });

  it("warns that the old link and code stop at once before replacing", () => {
    expect(REPLACE_WARNING).toBe("The old link and QR code stop working at once. Reprint any posters.");
    const html = renderToStaticMarkup(<ReplaceLinkConfirm busy={false} onConfirm={() => {}} onCancel={() => {}} />);
    expect(html).toContain(REPLACE_WARNING);
    expect(html).toContain("data-replace-confirm");
    expect(renderToStaticMarkup(<ReplaceLinkConfirm busy onConfirm={() => {}} onCancel={() => {}} />)).toMatch(/disabled=""[^>]*data-replace-confirm[^>]*>Replacing…/);
  });

  it("posts the replace with an empty body and returns the product with its new link", async () => {
    const seen: { url: string; method?: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      seen.push({ url, method: init.method, body: JSON.parse(String(init.body)) });
      return Response.json({ ...live, intake_url: `${INTAKE}x` });
    });
    expect((await replaceIntakeLink(live.id)).intake_url).toBe(`${INTAKE}x`);
    expect(seen).toEqual([{ url: `/api/v1/programs/${live.id}/intake/replace`, method: "POST", body: {} }]);
    await expect(replaceIntakeLink("../billing")).rejects.toThrow();
    expect(seen).toHaveLength(1);
  });
});

describe("the product page", () => {
  it("puts the claim link card where the bulk links were", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={live} canAdmin companyName="Acme" />);
    expect(html).toContain('<h2 id="claim-link" class="section-title mb-3">Claim link</h2>');
    expect(html).toContain("data-product-link-card");
    expect(html).not.toMatch(/issue claim links|Number of links|Download CSV/i);
    expect(html).not.toContain("data-paused-banner");
    // The rules note waits for the browser's memory, so the server renders an empty slot that takes no room.
    expect(html).toContain('<div class="mt-5 empty:hidden"></div>');
  });

  it("pauses an open product the plan does not cover, with the way to Billing", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={paused} canAdmin />);
    const text = html.replace(/<[^>]+>/g, "");
    expect(text).toContain("Paused. Your plan covers one product. Upgrade under Billing to take claims here again.");
    expect(html).toContain('href="/app/billing"');
    expect(html).toContain('data-no-claim-link="paused"');
    expect(html).not.toContain("data-product-link-card");
  });

  it("asks for the refund amount on a draft refund product without a price", () => {
    const html = renderToStaticMarkup(<ProgramOverview program={needsAmount} canAdmin />);
    expect(html).toContain("data-refund-amount-banner");
    expect(html).toContain("Set the refund amount</p>");
    expect(html).toContain("cannot take claims until the price range is set");
    expect(html).toContain(`href="/app/programs/${base.id}/settings#identification-rules"`);
    expect(html).not.toContain("data-draft-banner");
    expect(html).not.toContain("empty:hidden"); // the rules note is for open products
  });

  it("says plainly that a closed or draft product has no link", () => {
    expect(renderToStaticMarkup(<ProgramOverview program={closed} />)).toContain("Its claim link, and every claimant’s private link, no longer work.");
    const d = renderToStaticMarkup(<ProgramOverview program={draft} />);
    expect(d).toContain("data-draft-banner");
    expect(d).toContain("No claim link yet. It appears once the product opens.");
    expect(renderToStaticMarkup(<ProductBanner program={closed} />)).toBe("");
    expect(renderToStaticMarkup(<ProductBanner program={live} />)).toBe("");
  });

  it("reads the company name for the poster, and nothing for a malformed id", async () => {
    vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
    vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return new URL(url).pathname === "/v1/me"
        ? Response.json({ operator: false, email: "owner@example.com", firm: { id: "f", name: "Acme", onboarded: true, plan: "free", effective_plan: "free", subscription_status: null, approved: true, active_products: 1, product_limit: 1 } })
        : Response.json(live);
    });
    const html = withQuery(await ProgramPage({ params: Promise.resolve({ id: live.id }) }));
    expect(urls.sort()).toEqual([`http://127.0.0.1:8000/v1/me`, `http://127.0.0.1:8000/v1/programs/${live.id}`]);
    expect(html).toContain("data-product-link-card");
    expect(html).toContain("← Products");
    urls.length = 0;
    await ProgramPage({ params: Promise.resolve({ id: "not-an-id" }) });
    expect(urls).toEqual([]);
  });
});

describe("the check-the-rules note", () => {
  const memory = (): NoteStore & { data: Map<string, string> } => {
    const data = new Map<string, string>();
    return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
  };

  it("links to the product's settings and can be dismissed", () => {
    const html = renderToStaticMarkup(<RulesCheckNote programId={base.id} onDismiss={() => {}} />);
    expect(html.replace(/<[^>]+>/g, "")).toContain("The rules were read from the recall notice automatically. Check them.");
    expect(html).toContain(`href="/app/programs/${base.id}/settings"`);
    expect(html).toContain("data-rules-note-dismiss");
  });

  it("is remembered per product in this browser", () => {
    const store = memory();
    expect(rulesNoteDismissed(store, "p1")).toBe(false);
    dismissRulesNote(store, "p1");
    expect(store.data.get(`${RULES_NOTE_PREFIX}p1`)).toBe("1");
    expect(rulesNoteDismissed(store, "p1")).toBe(true);
    expect(rulesNoteDismissed(store, "p2")).toBe(false);
  });

  it("shows again, without failing, when storage is missing or throws", () => {
    const broken: NoteStore = {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
    };
    expect(rulesNoteDismissed(broken, "p1")).toBe(false);
    expect(() => dismissRulesNote(broken, "p1")).not.toThrow();
    expect(rulesNoteDismissed(null, "p1")).toBe(false);
    expect(() => dismissRulesNote(null, "p1")).not.toThrow();
    expect(browserStore()).toBeNull(); // no window on the server
    expect(renderToStaticMarkup(<RulesCheckReminder programId="p1" />)).toBe("");
  });
});

describe("product settings", () => {
  it("words the plan and refund-amount refusals of an open", () => {
    expect(lifecycleProblemSentence({ status: 402, detail: "Free covers one product. Upgrade to Growth to add another." })).toBe(PLAN_FULL_SENTENCE);
    expect(PLAN_FULL_SENTENCE).toBe("Free covers one product. Upgrade to Growth to add another.");
    expect(lifecycleProblemSentence({ status: 409, detail: "set the refund amount (the highest price) in Rules and settings before opening" })).toBe(MISSING_REFUND_AMOUNT_SENTENCE);
    expect(MISSING_REFUND_AMOUNT_SENTENCE).toBe("Set the refund amount (the price range) before opening.");
    expect(lifecycleProblemSentence({ status: 409, detail: "a closed program cannot be reopened; open a new program" })).toBeUndefined();
    expect(lifecycleProblemSentence({ status: 500, detail: null })).toBeUndefined();
  });

  it("asks for the price range on a refund product without one", () => {
    expect(rulesWarnings(needsAmount)[0]).toContain("Set the refund amount");
    expect(rulesWarnings(live).join(" ")).not.toContain("refund amount");
    const html = renderToStaticMarkup(<ProgramSettings program={needsAmount} canAdmin />);
    expect(html).toContain("The product cannot take claims until the price range is set.");
    expect(html).toContain("Open the product");
  });

  it("carries no team, member, seat, or organization copy", () => {
    for (const p of [live, closed]) {
      for (const canAdmin of [true, false]) {
        const text = renderToStaticMarkup(<ProgramSettings program={p} canAdmin={canAdmin} />).replace(/<[^>]+>/g, " ");
        expect(text).not.toMatch(/\b(teammates?|team|members?|seats?|organi[sz]ations?)\b/i);
        expect(text).not.toMatch(/\bprograms?\b/i);
      }
    }
  });
});

describe("the products list", () => {
  it("marks a paused product on its card", () => {
    const html = renderToStaticMarkup(<ProgramCards programs={[paused, live]} />);
    expect(html.match(/data-product-note/g)).toHaveLength(1);
    expect(html).toContain("Paused: takes no new claims");
    expect(html).toContain('aria-label="Products"');
  });

  it("lists only what the API returns for the company, with no way to create one here", async () => {
    vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
    vi.stubEnv("RECALLOPS_DEV_ROLE", "admin");
    const urls: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      urls.push(url);
      return Response.json({ items: [live] });
    });
    const html = renderToStaticMarkup(await ProgramsPage());
    expect(urls).toEqual(["http://127.0.0.1:8000/v1/programs"]);
    expect(html).toContain("<h1");
    expect(html).toContain(">Products<");
    expect(html).not.toContain("data-new-program");
    expect(html).toContain("data-ask-recall");
  });
});

describe("drafts in the claims queue", () => {
  const counts = { flagged: 2, approved: 1, draft: 5 };

  it("offers Started, not submitted as the last filter, outside All", () => {
    const html = renderToStaticMarkup(<StatusFilter programId={base.id} active={null} counts={counts} />);
    const order = [...html.matchAll(/data-filter="([a-z_]+)"/g)].map((m) => m[1]);
    expect(order.at(-1)).toBe("draft");
    expect(html).toMatch(/data-filter="draft"[^>]*href="\/app\/programs\/[^"]+\/claims\?status=draft">Started, not submitted<span[^>]*>5</);
    expect(html).toMatch(/data-filter="all"[^>]*>All<span[^>]*>3</);
  });

  it("asks the API for drafts only through the filter", () => {
    expect(claimsApiPath(base.id, { status: null, cursor: null }).search).toBe("?limit=50");
    expect(claimsApiPath(base.id, { status: "draft", cursor: null }).search).toBe("?status=draft&limit=50");
  });

  it("explains an empty drafts view", () => {
    expect(emptyQueueTitle("draft")).toBe("No unsubmitted claims");
    const html = withQuery(
      <ClaimsQueue programId={base.id} params={{ status: "draft", cursor: null }} initialPage={{ ok: true, data: { items: [], next_cursor: null } }} initialProgram={live} loadedAt={0} />,
    );
    expect(html).toContain("Claims started from the product’s claim link wait here until the claimant submits them.");
  });
});

describe("the campaign carries the product's link", () => {
  const summary = (link: string | null, body: string) =>
    CampaignSummary.parse({
      registrations: 2,
      matched: 2,
      unmatched: 0,
      claim_link: link,
      notifications: { "email:draft": 1, "mail:sent": 2, "phone_text:sent": 1 },
      sample: [{ channel: "email", status: "draft", subject: "Safety recall", body }],
      simulated: true,
    });

  it("shows the link every notice carries, and counts what was marked sent", () => {
    const html = renderToStaticMarkup(<CampaignStatus summary={summary(INTAKE, `File your claim at ${INTAKE}.`)} />);
    expect(html).toContain("data-campaign-link");
    expect(html).toContain(INTAKE);
    expect(html).toMatch(/<dt class="eyebrow">Marked sent<\/dt><dd[^>]*>3</);
    expect(html).not.toContain("Claim links");
  });

  it("says the product takes no claims when there is no link", () => {
    const html = renderToStaticMarkup(<CampaignStatus summary={summary(null, "File your claim at {claim_link}.")} />);
    expect(html).toContain("data-campaign-no-link");
    expect(html).toContain("This product is not taking claims");
  });

  it("shows the link in place of a placeholder as text, and a marker when there is none", () => {
    const filled = renderToStaticMarkup(<SampleNotices sample={summary(INTAKE, "Go to {claim_link} now").sample} claimLink={INTAKE} />);
    expect(filled).toContain(`data-link-filled="true">${INTAKE}</span>`);
    expect(filled).not.toContain("<a ");
    const none = renderToStaticMarkup(<SampleNotices sample={summary(null, "Go to {claim_link} now").sample} claimLink={null} />);
    expect(none).toContain("claim link (not taking claims)");
    expect(none).not.toContain("{claim_link}");
  });
});

describe("private claim links and refunds", () => {
  it("sends a reissued link only to the email on the claim", () => {
    expect(sendOnlyTo("owner@example.com")).toBe("Send the new link only to the email on this claim, owner@example.com.");
    expect(sendOnlyTo(" ")).toContain("This claim has no email yet");
    const html = renderToStaticMarkup(<ReissuedLink link={`https://x.test/claim/${"t".repeat(40)}`} claimId={base.id} copied={false} onCopy={() => {}} email="owner@example.com" />);
    expect(html).toContain("Send the new link only to the email on this claim, owner@example.com.");
  });

  it("says a refund waits for the claimant's choice and becomes a check after the deadline", () => {
    const payout = (status: string, method = "stripe_connect"): PayoutView => ({ id: "p", amount_cents: 2999, currency: "usd", method, status }) as PayoutView;
    expect(renderToStaticMarkup(<PayoutPanel payout={payout("pending")} remedy="refund" />)).toContain("choose a bank transfer or a mailed check");
    expect(renderToStaticMarkup(<PayoutPanel payout={payout("onboarding")} remedy="refund" />)).toContain("If they do not finish by the deadline, a check is mailed.");
    expect(renderToStaticMarkup(<PayoutPanel payout={payout("transferred")} remedy="refund" />)).not.toContain("data-payout-waiting");
    expect(renderToStaticMarkup(<PayoutPanel payout={payout("pending")} remedy="refund" />)).toContain("Bank transfer (Stripe)");
  });

  it("never says a plan pays refunds by check only", () => {
    const files = [
      ...readdirSync(new URL("../src/components", import.meta.url)).filter((f) => /^(Campaign|ClaimDetailLive|ClaimPanels|ProductLinkCard|PayoutExports|ProgramOverview|ProgramSettings|ReissueLink)\.tsx$/.test(f)).map((f) => `../src/components/${f}`),
      "../src/app/app/payouts/page.tsx",
      "../src/lib/products.ts",
      "../src/lib/program-rules.ts",
    ];
    expect(files.length).toBeGreaterThanOrEqual(11);
    for (const f of files) {
      const text = readFileSync(new URL(f, import.meta.url), "utf8");
      expect(text, f).not.toMatch(/check only|only by (mailed )?check|pays? (refunds )?by check|free plan/i);
    }
  });
});
