// Plan v3, the claimant's side: one public claim link per product (/file/[token]), Start my claim with a private
// link remembered on the device, the private link card with its QR code, the label photo before Submit, both refund
// options on every plan with the bank-transfer deadline, and the company running the recall on every claim page.
import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import nextConfig from "../next.config";
import ClaimFormPage from "@/app/claim/[token]/page";
import ClaimStatusPage from "@/app/claim/[token]/status/page";
import ProductLinkLayout, { metadata as fileMetadata } from "@/app/file/layout";
import ProductLinkPage from "@/app/file/[token]/page";
import { StepNav, ClaimStepper } from "@/components/portal/ClaimStepper";
import { ClaimLinkCard } from "@/components/portal/ClaimLinkCard";
import { ConnectReturnCard } from "@/components/portal/ConnectReturn";
import { PayoutChoice } from "@/components/portal/PayoutChoice";
import { ClaimHeader, LinkNotActive, RecallContact } from "@/components/portal/RecallFacts";
import { StartClaim, StartClaimActions, startClaim } from "@/components/portal/StartClaim";
import { StatusView } from "@/components/portal/StatusView";
import { IntakeView, PortalView } from "@/lib/api-types";
import { isPlausibleIntakeToken, loadIntake } from "@/lib/intake";
import * as portal from "@/lib/portal";
import {
  DEVICE_MEMORY_MS,
  LABEL_PHOTO_REASON,
  claimPagePath,
  claimPhotoKinds,
  claimUrl,
  intakePath,
  localStore,
  longDate,
  payoutDueSentence,
  rememberStartedClaim,
  senderName,
  startLimitSentence,
  startSentence,
  startedClaim,
  submitBlockedReason,
  submitSentence,
  waitWords,
} from "@/lib/portal";
import { portalSentence } from "@/lib/portal-copy";

const FIRM = "00000000-0000-4000-8000-00000000d001";
const PROGRAM = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";
const INTAKE = `intake.${FIRM}.${PROGRAM}.1`;
const SIGNED_INTAKE = "A".repeat(90) + "w";
const CLAIM = `dev.${FIRM}.66dcc492-086f-5b59-b441-cbe001eb2e0b`;
const OTHER_CLAIM = `dev.${FIRM}.768dc2db-e31c-5ee6-8062-665cf67dfdd1`;

const base = PortalView.parse(JSON.parse(readFileSync(new URL("./fixtures/portal-view.json", import.meta.url), "utf8")));
const withCompany = { ...base, program: { ...base.program, company_name: "Example Office Co.", contact: "Call Example Office Co. at 800-555-0100, 8 a.m. to 5 p.m. ET." } };
const intakeView: IntakeView = IntakeView.parse({
  company_name: "Example Office Co.",
  title: "Example Thermal Laminators Recalled Due to Fire Hazard",
  recall_number: "27024",
  models: ["TL1302Z", "TL901X"],
  model_label_location: "on the bottom of the laminator",
  sold_at: ["Walmart", "Target", "example.com"],
  remedy_type: "refund",
  destruction_instruction: "cut the power cord",
  required_photos: ["label", "destruction"],
  contact: "Call Example Office Co. at 800-555-0100, 8 a.m. to 5 p.m. ET.",
});

function memory(): Storage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, length: 0, clear: () => data.clear(), key: () => null, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v), removeItem: (k) => void data.delete(k) } as Storage & { data: Map<string, string> };
}
const throwing = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("full"); }, removeItem: () => { throw new Error("x"); } };

/** The opening tag of the first element carrying `marker`. */
function tag(html: string, marker: string): string {
  const at = html.indexOf(marker);
  expect(at, `${marker} not rendered`).toBeGreaterThan(-1);
  return html.slice(html.lastIndexOf("<", at), html.indexOf(">", at) + 1);
}

let calls: Array<{ url: string; method: string; headers: Headers; body: unknown }>;
function serve(respond: (url: string) => Response) {
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    calls.push({ url, method: init.method ?? "GET", headers: new Headers(init.headers), body: init.body });
    return respond(url);
  });
}
const params = (token: string) => ({ params: Promise.resolve({ token }) });
const problem = (status: number, headers: Record<string, string> = {}) =>
  Response.json({ title: "x", status, detail: "d", correlation_id: `c-${status}` }, { status, headers });

beforeEach(() => {
  calls = [];
  vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", FIRM);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("product link tokens", () => {
  it("accepts the dev form and the 91-character signed form, nothing else", () => {
    expect(isPlausibleIntakeToken(INTAKE)).toBe(true);
    expect(isPlausibleIntakeToken(`intake.${FIRM}.${PROGRAM}.123456789`)).toBe(true);
    expect(isPlausibleIntakeToken(SIGNED_INTAKE)).toBe(true);
    expect(isPlausibleIntakeToken("a-_Z9".repeat(18) + "x")).toBe(true);
    for (const bad of [
      "A".repeat(90),
      "A".repeat(92),
      "A".repeat(89) + "==",
      "A".repeat(90) + "/",
      `intake.${FIRM}.${PROGRAM}.0`,
      `intake.${FIRM}.${PROGRAM}`,
      `intake.${FIRM.toUpperCase()}.${PROGRAM}.1`,
      `intake.${FIRM}.../../v1.1`,
      CLAIM,
      "",
    ]) {
      expect(isPlausibleIntakeToken(bad), bad).toBe(false);
    }
  });

  it("builds the API and page paths", () => {
    expect(intakePath(INTAKE)).toBe(`/portal/intake/${INTAKE}`);
    expect(claimPagePath(CLAIM)).toBe(`/claim/${CLAIM}`);
    expect(claimUrl("https://recallops.example/", CLAIM)).toBe(`https://recallops.example/claim/${CLAIM}`);
  });
});

describe("loadIntake", () => {
  it("never sends a malformed link and answers it as not found", async () => {
    serve(() => Response.json(intakeView));
    const r = await loadIntake("../../v1/programs");
    expect(r).toMatchObject({ ok: false, problem: { status: 404 } });
    expect(calls).toHaveLength(0);
  });

  it("reads the product with the link alone, no console identity", async () => {
    serve(() => Response.json(intakeView));
    const r = await loadIntake(INTAKE);
    expect(r.ok && r.data.company_name).toBe("Example Office Co.");
    expect(calls[0]!.url).toBe(`http://127.0.0.1:8000/portal/intake/${INTAKE}`);
    expect(calls[0]!.headers.get("x-dev-firm-id")).toBeNull();
    expect(calls[0]!.headers.get("authorization")).toBeNull();
  });
});

describe("/file/[token]", () => {
  it("shows the company, the recall, the products, the remedy, the photos, the contact, and one action", async () => {
    serve(() => Response.json(intakeView));
    const out = renderToStaticMarkup(await ProductLinkPage(params(INTAKE)));
    expect(out).toContain('<span class="font-medium text-ink">Example Office Co.</span> is running this recall.');
    expect(out).toContain("Example Thermal Laminators Recalled Due to Fire Hazard</h1>");
    expect(out).toContain("Recall number 27024");
    expect(out).toContain("TL1302Z");
    expect(out).toContain("TL901X");
    expect(out).toContain("The model number is on the bottom of the laminator.");
    expect(out).toContain("Walmart, Target, and example.com");
    expect(out).toContain("If your claim is approved, you receive a refund.");
    expect(out).toContain("a bank transfer or a mailed check");
    expect(out).toContain("Label photo.");
    expect(out).toContain("First do this: cut the power cord.");
    expect(out).toContain("Questions about this recall");
    expect(out).toContain("800-555-0100");
    expect(out).toContain(">Start my claim</button>");
    // The server cannot see this device's memory, so it never offers Continue in its HTML.
    expect(out).not.toContain("Continue your claim");
    expect(out).not.toMatch(/organization|teammate|firm/i);
  });

  it("asks for the label photo first even when the program lists other photos only", async () => {
    serve(() => Response.json({ ...intakeView, required_photos: ["receipt"] }));
    const out = renderToStaticMarkup(await ProductLinkPage(params(INTAKE)));
    const photos = out.slice(out.indexOf("data-intake-photos"));
    expect(photos.indexOf("Label photo.")).toBeGreaterThan(-1);
    expect(photos.indexOf("Label photo.")).toBeLessThan(photos.indexOf("Receipt photo."));
  });

  it("leaves out where it was sold and the refund choice when the notice has neither", async () => {
    serve(() => Response.json({ ...intakeView, sold_at: [], remedy_type: "repair", models: [], recall_number: null, contact: null }));
    const out = renderToStaticMarkup(await ProductLinkPage(params(INTAKE)));
    expect(out).not.toContain("Where it was sold");
    expect(out).not.toContain("a bank transfer or a mailed check");
    expect(out).toContain("The notice does not list model numbers.");
    expect(out).not.toContain("Recall number");
    expect(out).not.toContain("Questions about this recall");
  });

  it("shows a replaced, closed, or unknown link as not active, calmly", async () => {
    serve(() => problem(404));
    const out = renderToStaticMarkup(await ProductLinkPage(params(INTAKE)));
    expect(out).toContain("This claim link is not active</h1>");
    expect(out).toContain("The company may have replaced it with a new link or closed it.");
    expect(out).toContain("Use the newest link the company shared, or contact them.");
    expect(out).not.toContain('role="alert"');
    expect(out).not.toContain("Start my claim");
  });

  it("answers a malformed link locally as not active", async () => {
    serve(() => Response.json(intakeView));
    const out = renderToStaticMarkup(await ProductLinkPage(params("..%2F..%2Fv1")));
    expect(calls).toHaveLength(0);
    expect(out).toContain("This claim link is not active");
  });

  it("explains other failures in plain words with the reference", async () => {
    serve(() => problem(503));
    const out = renderToStaticMarkup(await ProductLinkPage(params(INTAKE)));
    expect(out).toContain("The service is busy");
    expect(out).toContain("c-503");
    expect(out).not.toContain("Error 503");
  });

  it("has its own frame: no Clerk, never indexed, no referrer", () => {
    expect(fileMetadata.robots).toEqual({ index: false, follow: false });
    expect(fileMetadata.referrer).toBe("no-referrer");
    const html = renderToStaticMarkup(<ProductLinkLayout>x</ProductLinkLayout>);
    expect(html).toContain("Product recall claim");
    expect(html).not.toMatch(/clerk/i);
  });

  it("sends no referrer and no indexing as headers too, like claim pages", async () => {
    const rules = await nextConfig.headers!();
    const file = rules.find((r) => r.source === "/file/:path*");
    const claim = rules.find((r) => r.source === "/claim/:path*");
    expect(file?.headers).toEqual(claim?.headers);
    expect(file?.headers).toEqual(
      expect.arrayContaining([
        { key: "Referrer-Policy", value: "no-referrer" },
        { key: "X-Robots-Tag", value: "noindex, nofollow" },
      ]),
    );
  });
});

describe("starting a claim", () => {
  it("posts an empty JSON body to the product link and returns the private claim link", async () => {
    serve(() => Response.json({ claim_id: "c", claim_token: CLAIM, claim_url: `https://x.example/claim/${CLAIM}` }, { status: 201 }));
    expect(await startClaim(INTAKE)).toEqual({ kind: "started", claimToken: CLAIM });
    expect(calls[0]).toMatchObject({ url: `/api/portal/intake/${INTAKE}`, method: "POST", body: "{}" });
    expect(calls[0]!.headers.get("content-type")).toBe("application/json");
  });

  it("reads a 404 as a link that stopped while the page was open", async () => {
    serve(() => problem(404));
    expect(await startClaim(INTAKE)).toEqual({ kind: "inactive" });
  });

  it("keeps Retry-After from a 429 and says it in minutes", async () => {
    serve(() => problem(429, { "retry-after": "720" }));
    const outcome = await startClaim(INTAKE);
    expect(outcome).toMatchObject({ kind: "problem", problem: { status: 429, retryAfterSeconds: 720 } });
    if (outcome.kind !== "problem") throw new Error("expected a problem");
    expect(startSentence(outcome.problem)).toBe("Too many claims were started from this link or this network in a short time. Try again in 12 minutes.");
  });

  it("refuses an answer whose claim link could change the page path", async () => {
    serve(() => Response.json({ claim_id: "c", claim_token: "../../app", claim_url: "x" }, { status: 201 }));
    expect(await startClaim(INTAKE)).toMatchObject({ kind: "problem", problem: { status: 502 } });
  });

  it("turns a dropped connection into a status 0 problem", async () => {
    vi.stubGlobal("fetch", async () => {
      throw new TypeError("fetch failed");
    });
    expect(await startClaim(INTAKE)).toMatchObject({ kind: "problem", problem: { status: 0 } });
  });

  it("words waits for people", () => {
    expect(waitWords(null)).toBe("a few minutes");
    expect(waitWords(0)).toBe("1 minute");
    expect(waitWords(30)).toBe("1 minute");
    expect(waitWords(61)).toBe("2 minutes");
    expect(waitWords(3540)).toBe("59 minutes");
    expect(waitWords(3600)).toBe("1 hour");
    expect(waitWords(3601)).toBe("2 hours");
    expect(startLimitSentence(undefined)).toContain("Try again in a few minutes.");
    expect(startSentence({ status: 503, retryAfterSeconds: null })).toBe(portalSentence(503));
  });
});

describe("Start my claim buttons", () => {
  const actions = (over: Partial<Parameters<typeof StartClaimActions>[0]>) =>
    renderToStaticMarkup(<StartClaimActions remembered={null} busy={false} problem={null} onStart={() => {}} {...over} />);

  it("offers one primary action on a device that started nothing here", () => {
    const out = actions({});
    expect(tag(out, "data-start-first")).toContain("bg-accent");
    expect(out).toContain(">Start my claim</button>");
    expect(out).not.toContain("Continue your claim");
    expect(tag(actions({ busy: true }), "data-start-first")).toContain('disabled=""');
    expect(actions({ busy: true })).toContain("Starting your claim…");
  });

  it("makes Continue primary and a new claim secondary when this device started one", () => {
    const out = actions({ remembered: CLAIM });
    expect(tag(out, "data-continue-claim")).toContain(`href="/claim/${CLAIM}"`);
    expect(tag(out, "data-continue-claim")).toContain("bg-accent");
    expect(tag(out, "data-start-new")).toContain("border-line-strong");
    expect(tag(out, "data-start-new")).not.toContain("bg-accent");
    expect(out.indexOf("Continue your claim")).toBeLessThan(out.indexOf("Start a new claim"));
    expect(out).toContain("You already started a claim from this link on this device.");
  });

  it("says a 429 wait in minutes and never as seconds", () => {
    const out = actions({ problem: { status: 429, title: "Too Many Requests", detail: "rate limited", correlationId: "c-429", retryAfterSeconds: 90 } });
    expect(out).toContain("Try again in 2 minutes.");
    expect(out).not.toContain("90 seconds");
    expect(out).toContain("c-429");
    expect(out).not.toContain("rate limited");
  });

  it("renders the first-visit state on the server", () => {
    expect(renderToStaticMarkup(<StartClaim token={INTAKE} />)).toContain("Start my claim");
  });

  it("states a stopped link without an error box", () => {
    const out = renderToStaticMarkup(<LinkNotActive heading="h2" />);
    expect(out).toContain("This claim link is not active</h2>");
    expect(out).not.toContain('role="alert"');
  });
});

describe("what this device remembers", () => {
  const NOW = Date.parse("2026-10-21T12:00:00Z");

  it("remembers the claim started from each product link, the newest one per link", () => {
    const s = memory();
    expect(startedClaim(s, INTAKE, NOW)).toBeNull();
    rememberStartedClaim(s, INTAKE, CLAIM, NOW);
    expect(startedClaim(s, INTAKE, NOW + 1000)).toBe(CLAIM);
    expect(startedClaim(s, SIGNED_INTAKE, NOW)).toBeNull();
    rememberStartedClaim(s, INTAKE, OTHER_CLAIM, NOW + 2000);
    expect(startedClaim(s, INTAKE, NOW + 3000)).toBe(OTHER_CLAIM);
  });

  it("stops offering a started claim after the device memory runs out", () => {
    const s = memory();
    rememberStartedClaim(s, INTAKE, CLAIM, NOW);
    expect(startedClaim(s, INTAKE, NOW + DEVICE_MEMORY_MS)).toBe(CLAIM);
    expect(startedClaim(s, INTAKE, NOW + DEVICE_MEMORY_MS + 1)).toBeNull();
  });

  it("ignores corrupted or hostile entries and never stores a token that could change a path", () => {
    const s = memory();
    s.setItem(`recallops.intake.${INTAKE}`, "{not json");
    expect(startedClaim(s, INTAKE, NOW)).toBeNull();
    s.setItem(`recallops.intake.${INTAKE}`, JSON.stringify({ claim_token: "../../v1/programs", at: NOW }));
    expect(startedClaim(s, INTAKE, NOW)).toBeNull();
    s.setItem(`recallops.intake.${INTAKE}`, JSON.stringify({ claim_token: CLAIM }));
    expect(startedClaim(s, INTAKE, NOW)).toBeNull();
    s.setItem(`recallops.intake.${INTAKE}`, "null");
    expect(startedClaim(s, INTAKE, NOW)).toBeNull();
    const fresh = memory();
    rememberStartedClaim(fresh, INTAKE, "a/b", NOW);
    rememberStartedClaim(fresh, INTAKE, "tok en", NOW);
    expect(fresh.data.size).toBe(0);
  });

  it("works with blocked or missing site data", () => {
    expect(startedClaim(throwing, INTAKE, NOW)).toBeNull();
    expect(() => rememberStartedClaim(throwing, INTAKE, CLAIM, NOW)).not.toThrow();
    expect(() => rememberStartedClaim(null, INTAKE, CLAIM, NOW)).not.toThrow();
    expect(startedClaim(null, INTAKE, NOW)).toBeNull();
    vi.stubGlobal(
      "window",
      Object.defineProperty({}, "localStorage", {
        get() {
          throw new DOMException("The operation is insecure.", "SecurityError");
        },
      }),
    );
    expect(localStore()).toBeNull();
  });

  it("tells a claimant who lost the claim to open their private link", () => {
    const out = renderToStaticMarkup(<ConnectReturnCard kind="return" lost />);
    expect(out).toContain("Open your private claim link to see your refund status.");
    expect(out).not.toMatch(/firm/i);
  });
});

describe("the private claim link card", () => {
  const out = renderToStaticMarkup(<ClaimLinkCard token={CLAIM} />);

  it("shows the link with Copy, warns that it is the only way back, and folds the QR away", () => {
    expect(out).toContain("Your private claim link</h2>");
    expect(out).toContain("This link is the only way back to this claim.");
    expect(out).toContain("Do not share it with anyone else");
    // The server knows no origin, so its HTML carries the path; the browser shows the full address.
    expect(out).toContain(`/claim/${CLAIM}</p>`);
    expect(out).toContain(">Copy link</button>");
    expect(out).toContain("<summary");
    expect(out).toContain("Continue on another device</summary>");
    expect(out).not.toContain("<details open");
    expect(out).toContain('aria-live="polite"');
  });

  it("offers Share only where the browser has a share sheet (none on the server)", () => {
    expect(out).not.toContain("data-share-link");
  });

  it("is on the claim's first step and on its status page", () => {
    expect(renderToStaticMarkup(<ClaimStepper token={CLAIM} view={base} />)).toContain("data-claim-link-card");
    expect(renderToStaticMarkup(<StatusView token={CLAIM} view={{ ...base, status: "submitted" }} />)).toContain("data-claim-link-card");
  });
});

describe("the company running the recall", () => {
  it("is named on the claim form and the status page, with the notice's contact", async () => {
    serve(() => Response.json(withCompany));
    const form = renderToStaticMarkup(await ClaimFormPage(params(CLAIM)));
    expect(form).toContain('<span class="font-medium text-ink">Example Office Co.</span> is running this recall.');
    expect(form).toContain("Questions about this recall");
    expect(form).toContain("800-555-0100");
    serve(() => Response.json({ ...withCompany, status: "submitted" }));
    const status = renderToStaticMarkup(await ClaimStatusPage(params(CLAIM)));
    expect(status).toContain("Example Office Co.</span> is running this recall.");
    expect(status).toContain("800-555-0100");
  });

  it("signs the company's messages with its name", () => {
    expect(senderName("firm", "Example Office Co.")).toBe("Example Office Co.");
    expect(senderName("firm", "  ")).toBe("The company");
    expect(senderName("firm", null)).toBe("The company");
    expect(senderName("claimant", "Example Office Co.")).toBe("You");
    const out = renderToStaticMarkup(<StatusView token={CLAIM} view={{ ...withCompany, status: "submitted", messages: [{ sender: "firm", text: "Where did you buy it?", at: "2026-10-21T20:55:00Z" }] }} />);
    expect(out).toContain("Example Office Co. · ");
  });

  it("leaves out what the API does not send", () => {
    const out = renderToStaticMarkup(<ClaimHeader title="T" companyName={null} />);
    expect(out).not.toContain("is running this recall");
    expect(renderToStaticMarkup(<RecallContact contact="  " />)).toBe("");
  });
});

describe("refunds: both ways on every plan", () => {
  it("offers a bank transfer and a mailed check while the refund waits for a choice", () => {
    for (const current of ["pending", "onboarding", "failed"]) {
      const out = renderToStaticMarkup(<PayoutChoice token={CLAIM} current={current} />);
      expect(out).toContain("A mailed check");
      expect(out).toContain("A bank transfer (set up on Stripe");
    }
  });

  it("has no plan fallback left: no 402 rule, no check-only copy", () => {
    expect(Object.keys(portal)).not.toContain("methodAfterPayoutError");
    expect(portalSentence(402)).toBe("That did not work. Try again.");
    for (const f of ["lib/portal.ts", "lib/portal-copy.ts", "components/portal/PayoutChoice.tsx"]) {
      const src = readFileSync(new URL(`../src/${f}`, import.meta.url), "utf8");
      expect(src, f).not.toMatch(/\b402\b|pays refunds by mailed check|check only/);
    }
  });

  it("tells a refund claimant on the claim form's first step that the choice comes after approval", () => {
    const refund = renderToStaticMarkup(<ClaimStepper token={CLAIM} view={base} />);
    expect(refund).toContain("After approval you choose how to receive it: a bank transfer or a mailed check.");
    const repair = renderToStaticMarkup(<ClaimStepper token={CLAIM} view={{ ...base, program: { ...base.program, remedy_type: "repair" } }} />);
    expect(repair).not.toContain("a bank transfer or a mailed check");
  });

  it("formats the deadline as a long en-US date, read as that calendar day", () => {
    expect(longDate("2026-11-04")).toBe("November 4, 2026");
    expect(longDate("2026-01-01")).toBe("January 1, 2026");
    expect(longDate("soon")).toBe("soon");
  });

  it("says when a waiting bank transfer becomes a mailed check", () => {
    expect(payoutDueSentence("pending", "2026-11-04")).toBe("Choose by November 4, 2026. After that we mail a check to your address.");
    expect(payoutDueSentence("onboarding", "2026-11-20")).toBe("Finish your bank setup by November 20, 2026. After that we mail a check to your address.");
    expect(payoutDueSentence("pending", null)).toBeNull();
    expect(payoutDueSentence("pending", undefined)).toBeNull();
  });

  it("shows the deadline on the status page when the API sends one", () => {
    const out = renderToStaticMarkup(<StatusView token={CLAIM} view={{ ...base, status: "approved", payout_status: "pending", payout_choice_due: "2026-11-04" }} />);
    expect(out).toContain("Choose by November 4, 2026. After that we mail a check to your address.");
    expect(out).toContain("How do you want to receive your refund?");
    const none = renderToStaticMarkup(<StatusView token={CLAIM} view={{ ...base, status: "approved", payout_status: "pending", payout_choice_due: null }} />);
    expect(none).not.toContain("data-payout-due");
  });
});

describe("the label photo before Submit", () => {
  const nav = (over: Partial<Parameters<typeof StepNav>[0]>) =>
    renderToStaticMarkup(<StepNav showBack isReview submitting={false} photosBusy={false} onBack={() => {}} onContinue={() => {}} onSubmit={() => {}} {...over} />);

  it("always asks for the label photo, first", () => {
    expect(claimPhotoKinds(["label", "destruction"])).toEqual(["label", "destruction"]);
    expect(claimPhotoKinds(["destruction", "label", "receipt"])).toEqual(["label", "destruction", "receipt"]);
    expect(claimPhotoKinds(["receipt"])).toEqual(["label", "receipt"]);
    expect(claimPhotoKinds([])).toEqual(["label"]);
  });

  it("blocks Submit until the label photo is saved", () => {
    expect(submitBlockedReason([])).toBe(LABEL_PHOTO_REASON);
    expect(submitBlockedReason(["destruction"])).toBe(LABEL_PHOTO_REASON);
    expect(submitBlockedReason(["label"])).toBeNull();
  });

  it("renders Submit disabled with the reason, and enabled once the label is saved", () => {
    const blocked = nav({ submitBlocked: LABEL_PHOTO_REASON });
    expect(tag(blocked, "data-submit")).toContain('disabled=""');
    expect(tag(blocked, "data-submit")).toContain('aria-describedby="step-nav-blocked"');
    expect(blocked).toContain("Add a photo of the product&#x27;s label before you submit.");
    const open = nav({ submitBlocked: null });
    expect(tag(open, "data-submit")).not.toContain('disabled=""');
    expect(open).not.toContain("data-blocked-reason");
    // Continue on earlier steps is never held back by the label rule.
    expect(tag(nav({ isReview: false, submitBlocked: LABEL_PHOTO_REASON }), "data-continue")).not.toContain('disabled=""');
  });

  it("shows the API's own sentence when it still answers 409", () => {
    expect(submitSentence({ status: 409, detail: "add a photo of the product's label before submitting" })).toBe("Add a photo of the product's label before submitting.");
    expect(submitSentence({ status: 409, detail: null })).toBe(portalSentence(409));
    expect(submitSentence({ status: 503, detail: "label storage down" })).toBe(portalSentence(503));
  });

  it("puts the label photo on the claim form's photo step", () => {
    const src = readFileSync(new URL("../src/components/portal/ClaimStepper.tsx", import.meta.url), "utf8");
    expect(src).toContain("const photoKinds = claimPhotoKinds(program.required_photos);");
    expect(src).toContain("{photoKinds.map((kind) => (");
    expect(src).toContain("submitBlocked={submitBlocked}");
  });
});
