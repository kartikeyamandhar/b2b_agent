// Review fixes on plan v3's website: the claimant's address on server-rendered portal reads (F6), claim memory on a
// shared device (F2), the plan-full words (F7, item 9), draft product copy (F8), the AI agent's limit (F9), labels and
// focus (F10), dead code (F12), and Approve when another company runs the recall. No test reaches the network.
import { readFileSync } from "node:fs";

import { isValidElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const page = vi.hoisted(() => ({ ip: "198.51.100.7" as string | null }));
vi.mock("next/headers", () => ({ headers: async () => new Headers(page.ip ? { "x-real-ip": page.ip } : {}) }));
vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import Home from "@/app/page";
import { Onboarding } from "@/app/app/welcome/Onboarding";
import { AccessProblem, AccessRequests, RecallResults, SLOT_TAKEN_SENTENCE } from "@/components/AccessRequests";
import { CompanyNameForm } from "@/components/BillingLive";
import { APPROVED_ELSEWHERE_REASON, OperatorQueue, RevokeConfirm } from "@/components/OperatorQueue";
import { ReplaceLinkConfirm } from "@/components/ProductLinkCard";
import { ClaimLinkCard } from "@/components/portal/ClaimLinkCard";
import { StartClaimActions } from "@/components/portal/StartClaim";
import * as access from "@/lib/access";
import { REVOKE_CONFIRM } from "@/lib/access";
import type { AccessRequest, AccessStatus, MeFirm, OperatorAccessRequest, PlanInfo, RecallSummary } from "@/lib/api-types";
import { IntakeView, PortalView } from "@/lib/api-types";
import { upstream } from "@/lib/api/backend";
import { loadPortal } from "@/lib/api/queries";
import { AGENT_LIMIT, planCard, planSummary } from "@/lib/billing";
import { focusOnOpen } from "@/lib/focus";
import { loadIntake } from "@/lib/intake";
import * as portal from "@/lib/portal";
import {
  DEVICE_MEMORY_MS,
  EMPTY_DRAFT,
  FORGET_LABEL,
  FORGOT_TEXT,
  forgetClaim,
  rememberConnectClaim,
  rememberStartedClaim,
  saveDraft,
  startedClaim,
  takeConnectClaim,
  type DeviceStore,
} from "@/lib/portal";

const API = "http://127.0.0.1:8000";
const SECRET = "s".repeat(40);
const FIRM_ID = "00000000-0000-4000-8000-00000000d001";
const PROGRAM = "0874f66f-bfb5-552b-bd93-6266f49c1d5a";
const INTAKE = `intake.${FIRM_ID}.${PROGRAM}.1`;
const OTHER_INTAKE = `intake.${FIRM_ID}.${PROGRAM}.2`;
const CLAIM_ID = "66dcc492-086f-5b59-b441-cbe001eb2e0b";
const CLAIM = `dev.${FIRM_ID}.${CLAIM_ID}`;
const OTHER_CLAIM = `dev.${FIRM_ID}.768dc2db-e31c-5ee6-8062-665cf67dfdd1`;

const portalView = PortalView.parse(JSON.parse(readFileSync(new URL("./fixtures/portal-view.json", import.meta.url), "utf8")));
const intakeView = IntakeView.parse({
  company_name: "Example Office Co.",
  title: "Example Thermal Laminators Recalled Due to Fire Hazard",
  recall_number: "27024",
  models: ["TL1302Z"],
  model_label_location: "on the bottom of the laminator",
  sold_at: ["Walmart"],
  remedy_type: "refund",
  destruction_instruction: "cut the power cord",
  required_photos: ["label", "destruction"],
  contact: "Call Example Office Co. at 800-555-0100.",
});

const FIRM: MeFirm = { id: FIRM_ID, name: "Acme Kettles", onboarded: true, plan: "free", effective_plan: "free", subscription_status: null, approved: true, active_products: 1, product_limit: 1 };
const REQ: AccessRequest = { id: "a1", recall_id: "r1", title: "Acme Kettle Recall", typed: false, status: "approved", created_at: "2026-10-22T00:10:00Z", decided_at: "2026-10-22T01:00:00Z" };
const status = (s: Partial<AccessStatus>): AccessStatus => ({ required: true, approved: false, operator: false, requests: [], ...s });
const FREE: PlanInfo = { id: "free", product_limit: 1, program_price_cents: null, included_claims: null, claim_price_cents: null, currency: "usd", refund_options: ["stripe_connect", "check_export"] };
const OP: OperatorAccessRequest = {
  id: "o1",
  firm_id: "f1",
  firm_name: "Acme Kettles",
  recall_id: "r1",
  recall_number: "26999",
  title: "Acme Recalls Kettles",
  typed: false,
  typed_company: null,
  status: "requested",
  requested_by: "user_x",
  requester_domain: "acme.example",
  requester_email: "ops@acme.example",
  requester_email_verified: true,
  requester_test_address: false,
  firm_plan: "free",
  firm_active_products: 0,
  notice_company: "Acme",
  notice_sites: [],
  domain_matches: true,
  approved_elsewhere: null,
  decided_by: null,
  decided_at: null,
  note: null,
  created_at: "2026-10-22T00:10:00Z",
};

/** A Storage stand-in that can list its keys, like the browser's. */
function store(): DeviceStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    get length() {
      return data.size;
    },
    key: (i: number) => [...data.keys()][i] ?? null,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}
const throwing = {
  length: 1,
  key: () => {
    throw new Error("denied");
  },
  getItem: () => {
    throw new Error("denied");
  },
  setItem: () => {
    throw new Error("full");
  },
  removeItem: () => {
    throw new Error("x");
  },
};

const src = (path: string) => readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");

/** The opening tag of the first element carrying `marker`. */
function tag(html: string, marker: string): string {
  const at = html.indexOf(marker);
  expect(at, `${marker} not rendered`).toBeGreaterThan(-1);
  return html.slice(html.lastIndexOf("<", at), html.indexOf(">", at) + 1);
}

/** Every element in a rendered tree (function components are not called, so only hookless ones are walked). */
function elements(node: ReactNode): Array<{ type: unknown; props: Record<string, unknown> }> {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement(node)) return [];
  const props = node.props as Record<string, unknown>;
  return [{ type: node.type, props }, ...elements(props.children as ReactNode)];
}

let calls: Array<{ url: string; headers: Headers }>;
beforeEach(() => {
  calls = [];
  page.ip = "198.51.100.7";
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("server-rendered portal reads name the claimant to the API (F6)", () => {
  beforeEach(() => {
    vi.stubEnv("RECALLOPS_API_URL", API);
    vi.stubEnv("RECALLOPS_WEB_PROXY_SECRET", SECRET);
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      calls.push({ url, headers: new Headers(init.headers) });
      return Response.json(url.includes("/portal/intake/") ? intakeView : portalView);
    });
  });

  it("sends the page request's address and the shared secret from /file and /claim pages", async () => {
    expect((await loadIntake(INTAKE)).ok).toBe(true);
    expect((await loadPortal(CLAIM)).ok).toBe(true);
    expect(calls.map((c) => new URL(c.url).pathname)).toEqual([`/portal/intake/${INTAKE}`, `/portal/${CLAIM}`]);
    for (const c of calls) {
      expect(c.headers.get("x-recallops-client-ip"), c.url).toBe("198.51.100.7");
      expect(c.headers.get("x-recallops-web-secret"), c.url).toBe(SECRET);
    }
  });

  it("keeps the proxied request's own address for the /api route, and sends nothing when no address is known", async () => {
    await upstream("portal", `/portal/${CLAIM}`, "", { method: "GET", headers: new Headers({ "x-real-ip": "203.0.113.5" }) });
    expect(calls[0]!.headers.get("x-recallops-client-ip")).toBe("203.0.113.5");
    page.ip = null;
    await loadPortal(CLAIM);
    expect(calls[1]!.headers.get("x-recallops-client-ip")).toBeNull();
    expect(calls[1]!.headers.get("x-recallops-web-secret")).toBeNull();
  });
});

describe("claim memory on a shared device (F2)", () => {
  const NOW = Date.parse("2026-10-22T12:00:00Z");
  const HOUR = 60 * 60 * 1000;

  it("remembers a claim per product link for 48 hours, not 90 days", () => {
    expect(DEVICE_MEMORY_MS).toBe(48 * HOUR);
    const s = store();
    rememberStartedClaim(s, INTAKE, CLAIM, NOW);
    expect(startedClaim(s, INTAKE, NOW + 47 * HOUR)).toBe(CLAIM);
    expect(startedClaim(s, INTAKE, NOW + 49 * HOUR)).toBeNull();
  });

  it("keeps no last-claim memory, and a Stripe return uses only this tab's marker", () => {
    for (const gone of ["deviceClaim", "rememberDeviceClaim", "connectReturnClaim"]) expect(Object.keys(portal), gone).not.toContain(gone);
    expect(src("components/portal/ConnectReturn.tsx")).not.toMatch(/localStore|localStorage/);
    expect(src("components/portal/ClaimLinkCard.tsx")).not.toMatch(/useEffect|setItem|remember/);
    expect(src("components/portal/StartClaim.tsx")).not.toMatch(/rememberDeviceClaim/);
    const session = store();
    expect(takeConnectClaim(session)).toBeNull();
    rememberConnectClaim(session, CLAIM);
    expect(takeConnectClaim(session)).toBe(CLAIM);
    expect(takeConnectClaim(session)).toBeNull();
  });

  it("forgets every reference to one claim, and nothing else", () => {
    const local = store();
    const session = store();
    rememberStartedClaim(local, INTAKE, CLAIM, NOW);
    rememberStartedClaim(local, OTHER_INTAKE, CLAIM, NOW - 30 * 24 * HOUR); // an old one goes too
    rememberStartedClaim(local, `intake.${FIRM_ID}.${PROGRAM}.3`, OTHER_CLAIM, NOW);
    local.setItem("recallops.portal.last-claim", JSON.stringify({ claim_token: CLAIM, at: NOW }));
    local.setItem("another-site.thing", JSON.stringify({ claim_token: CLAIM, at: NOW }));
    rememberConnectClaim(session, CLAIM);
    saveDraft(session, CLAIM_ID, { ...EMPTY_DRAFT, name: "A Claimant" }, 2);

    expect(forgetClaim(local, session, CLAIM, CLAIM_ID)).toBe(true);
    expect(startedClaim(local, INTAKE, NOW)).toBeNull();
    expect([...local.data.keys()].sort()).toEqual(["another-site.thing", `recallops.intake.intake.${FIRM_ID}.${PROGRAM}.3`]);
    expect(session.data.size).toBe(0);
  });

  it("leaves another claim's Stripe marker alone and reports a browser that refused", () => {
    const session = store();
    rememberConnectClaim(session, OTHER_CLAIM);
    expect(forgetClaim(null, session, CLAIM)).toBe(true);
    expect(takeConnectClaim(session)).toBe(OTHER_CLAIM);
    expect(forgetClaim(throwing, throwing, CLAIM, CLAIM_ID)).toBe(false);
    expect(forgetClaim(null, null, CLAIM)).toBe(true);
  });

  it("offers Forget on the private link card", () => {
    const html = renderToStaticMarkup(<ClaimLinkCard token={CLAIM} claimId={CLAIM_ID} />);
    expect(tag(html, "data-forget-claim")).toMatch(/^<button type="button"/);
    expect(html).toContain(`${FORGET_LABEL}</button>`);
    expect(FORGET_LABEL).toBe("Forget this claim on this device");
  });

  it("offers Forget next to Continue your claim on a product link, and says what happened", () => {
    const html = renderToStaticMarkup(<StartClaimActions remembered={CLAIM} busy={false} problem={null} onStart={() => {}} onForget={() => {}} />);
    expect(html.indexOf("data-continue-claim")).toBeLessThan(html.indexOf("data-forget-claim"));
    expect(html.indexOf("data-forget-claim")).toBeLessThan(html.indexOf("data-start-new"));
    expect(html).toContain(`${FORGET_LABEL}</button>`);
    expect(tag(html, "data-forget-note")).toContain('aria-live="polite"');
    const after = renderToStaticMarkup(<StartClaimActions remembered={null} busy={false} problem={null} onStart={() => {}} onForget={() => {}} forgetNote={FORGOT_TEXT} />);
    expect(after).not.toContain("data-continue-claim");
    expect(after).toContain(FORGOT_TEXT);
    expect(after).toContain("data-start-first");
  });
});

describe("the plan-full words on Recall access (F7, item 9)", () => {
  it("answers a 402 with fixed words and Billing, whatever the detail says", () => {
    for (const detail of ["free plan: active product limit reached", null, "Free covers one product. Upgrade to Growth to add another."]) {
      const html = renderToStaticMarkup(<AccessProblem problem={{ status: 402, title: "Payment Required", detail, correlationId: null, retryAfterSeconds: null }} />);
      expect(html).toContain("Free covers one product. Upgrade to Growth to add another.");
      expect(html).toContain('href="/app/billing"');
      expect(html).not.toContain("limit reached");
    }
  });

  it("says the slot is in use or waiting, whichever holds it", () => {
    expect(SLOT_TAKEN_SENTENCE).toBe("Free covers one product at a time, and yours is in use or waiting for review. Upgrade to Growth to ask for another.");
    const inUse = renderToStaticMarkup(<AccessRequests status={status({})} firm={FIRM} />);
    const waiting = renderToStaticMarkup(<AccessRequests status={status({ requests: [{ ...REQ, status: "requested", program_id: null }] })} firm={{ ...FIRM, active_products: 0 }} />);
    for (const html of [inUse, waiting]) expect(html).toContain(SLOT_TAKEN_SENTENCE);
    expect(renderToStaticMarkup(<AccessRequests status={status({})} firm={{ ...FIRM, active_products: 0 }} />)).not.toContain("data-upgrade-prompt");
  });

  it("turns Request access off while the plan is full", () => {
    const results: RecallSummary[] = [
      { id: "r9", source: "cpsc", source_id: "27001", title: "Acme Recalls Toasters", recall_date: "2026-10-01", category: null, units_affected: 100, units_status: "ok", url: "https://www.cpsc.gov/" },
    ];
    const off = renderToStaticMarkup(<RecallResults results={results} requests={[]} disabled onRequest={() => {}} />);
    expect(tag(off, 'data-request-recall="r9"')).toContain('disabled=""');
    const on = renderToStaticMarkup(<RecallResults results={results} requests={[]} disabled={false} onRequest={() => {}} />);
    expect(tag(on, 'data-request-recall="r9"')).not.toContain('disabled=""');
  });
});

describe("a draft product on Recall access (F8)", () => {
  it("reads as not open yet, with the link to the product", () => {
    const html = renderToStaticMarkup(<AccessRequests status={status({ approved: true, requests: [{ ...REQ, program_id: "p2", program_status: "draft" }] })} />);
    const text = html.replace(/<[^>]+>/g, "");
    expect(text).toContain("Not open yet. Open the product to finish setting it up.");
    expect(html).toMatch(/href="\/app\/programs\/p2"[^>]*>Open the product<\/a>/);
    expect(text).not.toMatch(/refund amount/i);
  });
});

describe("what the AI agent does on Free (F9)", () => {
  it("promises a daily limit, never every claim", () => {
    expect(AGENT_LIMIT).toBe("The AI agent checks claims, up to a daily limit per product");
    expect(planCard(FREE).features).toContain(AGENT_LIMIT);
    const summary = planSummary({ effective_plan: "free", limits: { active_products: 1 } })!;
    expect(summary).toContain("up to a daily limit per product");
    const home = renderToStaticMarkup(<Home />).replace(/<[^>]+>/g, " ");
    expect(home).toContain("up to a daily limit per product");
    for (const text of [...planCard(FREE).features, summary, home]) expect(text).not.toMatch(/agent checks (every|each) claim/i);
  });
});

describe("labels and focus (F10)", () => {
  it("labels the company name on Billing with the button and help text outside the label", () => {
    const html = renderToStaticMarkup(<CompanyNameForm initialName="Acme Kettles" canManage />);
    const label = html.match(/<label[^>]*>[\s\S]*?<\/label>/)![0];
    expect(label).toContain('for="billing-company-name"');
    expect(label).not.toMatch(/<input|<button|see this name/);
    expect(tag(html, "data-company-name")).toMatch(/id="billing-company-name"[^>]*aria-describedby="billing-company-name-help"/);
    expect(html).toMatch(/id="billing-company-name-help"[^>]*>The operator and your claimants see this name\./);
  });

  it("labels the company name at setup with the help text outside the label", () => {
    const html = renderToStaticMarkup(<Onboarding plans={[FREE]} />);
    const label = html.match(/<label[^>]*for="onboarding-company-name"[^>]*>[\s\S]*?<\/label>/)![0];
    expect(label).not.toMatch(/<input|see this name/);
    expect(tag(html, 'id="onboarding-company-name"')).toContain('aria-describedby="onboarding-company-name-help"');
    expect(html).toMatch(/id="onboarding-company-name-help"[^>]*>The operator and your claimants see this name\./);
  });

  it("moves focus to the question when a revoke or replace confirmation opens", () => {
    const noop = () => {};
    for (const [tree, question] of [
      [RevokeConfirm({ busy: false, onConfirm: noop, onCancel: noop }), REVOKE_CONFIRM],
      [ReplaceLinkConfirm({ busy: false, onConfirm: noop, onCancel: noop }), "Replace this product’s claim link?"],
    ] as const) {
      const [group, ...inside] = elements(tree);
      expect(group!.props.role).toBe("group");
      const target = inside.find((e) => e.props.ref === focusOnOpen);
      expect(target, question).toBeDefined();
      expect(target!.type).toBe("p");
      expect(target!.props.tabIndex).toBe(-1);
      expect(target!.props.children).toEqual(expect.stringContaining(question.slice(0, 20)));
      // nothing focusable comes before it
      expect(inside.slice(0, inside.indexOf(target!)).some((e) => e.type === "button")).toBe(false);
    }
    const el = { focus: vi.fn() };
    focusOnOpen(el as unknown as HTMLElement);
    expect(el.focus).toHaveBeenCalledTimes(1);
    expect(() => focusOnOpen(null)).not.toThrow();
  });
});

describe("dead code (F12)", () => {
  it("has no typedReadyForProgram and no unused operator timeout", () => {
    expect(Object.keys(access)).not.toContain("typedReadyForProgram");
    expect(src("lib/api/queries.ts")).toMatch(/export function loadOperatorAccess\(env: Env = process\.env\): /);
  });
});

describe("Approve when another company runs the recall", () => {
  it("turns Approve off with the reason beside it, and keeps Reject", () => {
    const html = renderToStaticMarkup(<OperatorQueue requests={[{ ...OP, approved_elsewhere: "Demo Recall Co" }]} />);
    const approve = tag(html, 'data-operator-action="approved"');
    expect(approve).toContain('disabled=""');
    const reasonId = approve.match(/aria-describedby="([^"]+)"/)![1]!;
    const reason = tag(html, `id="${reasonId}"`);
    expect(html.slice(html.indexOf(reason) + reason.length)).toMatch(/^Another company runs this recall\. Reject this request, or revoke that company(?:'|&#x27;)s approval first\.</);
    expect(APPROVED_ELSEWHERE_REASON).toBe("Another company runs this recall. Reject this request, or revoke that company's approval first.");
    expect(tag(html, 'data-operator-action="rejected"')).not.toContain('disabled=""');
    expect(html).toContain("Demo Recall Co already runs this recall.");
  });

  it("leaves Approve on when no other company runs it", () => {
    const html = renderToStaticMarkup(<OperatorQueue requests={[OP]} />);
    expect(tag(html, 'data-operator-action="approved"')).not.toContain('disabled=""');
    expect(html).not.toContain("data-approve-blocked");
  });
});

describe("a claim the rules checklist approved while the agent was unavailable", () => {
  it("tells the reviewer why it waits and what to check", async () => {
    const { DecisionPanel } = await import("@/components/ClaimPanels");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const decision = { outcome: "approve", reasons: [], cited_rules: [], routed_to_human: true, decided_by: "rules_only", hold_reason: "agent_unavailable" };
    const html = renderToStaticMarkup(<DecisionPanel claimId="c1" decision={decision as never} decidedAt={null} />);
    expect(html).toContain("approved without reading the photos");
  });
});

describe("an approved company's name", () => {
  it("is read-only and says the operator renames it", async () => {
    const { CompanyNameForm } = await import("@/components/BillingLive");
    const { renderToStaticMarkup } = await import("react-dom/server");
    const locked = renderToStaticMarkup(<CompanyNameForm initialName="Kettle Works" canManage locked />);
    expect(locked).toContain("ask the operator");
    expect(locked).not.toContain(">Save<");
    expect(locked).toMatch(/<input[^>]*disabled/);
    const open = renderToStaticMarkup(<CompanyNameForm initialName="Kettle Works" canManage />);
    expect(open).toContain(">Save<");
  });
});
