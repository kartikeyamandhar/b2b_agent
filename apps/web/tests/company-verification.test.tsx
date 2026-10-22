// Company verification (decisions log 2026-10-22): the first live deploy let any signed-up organization run any
// recall. Now the console shows an unapproved company only its access requests and Billing, and the operator decides
// in a window of their own (plan v3: approving opens the company's product at once).
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: () => {}, push: () => {} }), usePathname: () => "/app/programs" }));

import { GATED_NAV, gateConsole, gateNav } from "@/components/AccessGate";
import { AppShell, CONSOLE_NAV } from "@/components/AppShell";
import { AccessRequests } from "@/components/AccessRequests";
import { OperatorQueue } from "@/components/OperatorQueue";
import { ProblemAlert } from "@/components/ProblemAlert";
import type { AccessRequest, AccessStatus, OperatorAccessRequest } from "@/lib/api-types";
import { accessBody, accessStatusLabel, domainHint, operatorActions, requestFor } from "@/lib/access";

const REQ: AccessRequest = {
  id: "a1",
  recall_id: "r1",
  title: "Primark Recalls 101 Dalmatians Coin Purses",
  typed: false,
  status: "requested",
  created_at: "2026-10-22T00:10:00Z",
  decided_at: null,
};
const status = (s: Partial<AccessStatus>): AccessStatus => ({ required: true, approved: false, operator: false, requests: [], ...s });
const ok = (s: AccessStatus) => async () => ({ ok: true as const, data: s });
const OP: OperatorAccessRequest = {
  id: "a1",
  firm_id: "f1",
  firm_name: "Not Really Primark",
  recall_id: "r1",
  recall_number: "26999",
  title: REQ.title,
  typed: false,
  typed_company: null,
  status: "requested",
  requested_by: "user_x",
  requester_domain: "gmail.com",
  notice_company: "Primark",
  notice_sites: [],
  domain_matches: false,
  approved_elsewhere: null,
  decided_by: null,
  decided_at: null,
  note: null,
  created_at: "2026-10-22T00:10:00Z",
};

describe("the console gate", () => {
  const child = <p data-console-child>queue</p>;
  const body = async (s: AccessStatus) => renderToStaticMarkup(<>{(await gateConsole(child, ok(s))).body}</>);

  it("shows only the access page to a company not approved yet", async () => {
    const html = await body(status({ requests: [REQ] }));
    expect(html).not.toContain("data-console-child");
    expect(html).toContain("Request access to your recall");
    expect(html).toContain("Waiting for review");
  });

  it("opens the console when approved, when not required, and when the status cannot be read", async () => {
    for (const s of [status({ approved: true }), status({ required: false })]) {
      expect(await body(s)).toContain("data-console-child");
      expect((await gateConsole(child, ok(s))).gated).toBe(false);
    }
    const down = async () => ({ ok: false as const, problem: { status: 503, title: "Down", detail: null, correlationId: null, retryAfterSeconds: null } });
    const gate = await gateConsole(child, down);
    expect(renderToStaticMarkup(<>{gate.body}</>)).toContain("data-console-child");
    expect(gate.gated).toBe(false);
  });

  it("no longer opens for the operator flag: the operator has no company (plan v3)", async () => {
    expect((await gateConsole(child, ok(status({ operator: true })))).gated).toBe(true);
  });

  it("leaves Billing and Recall access in the gated sidebar", async () => {
    expect(gateNav(await gateConsole(child, ok(status({}))))).toBe(GATED_NAV);
    expect(GATED_NAV.map((i) => i.href)).toEqual(["/app/billing", "/app/access"]);
    expect(gateNav(null)).toBe(CONSOLE_NAV);
  });
});

describe("the console sidebar", () => {
  it("has no operator item for anyone", () => {
    expect(renderToStaticMarkup(<AppShell identityNote="n" role="admin">x</AppShell>)).not.toContain('href="/operator"');
    expect(CONSOLE_NAV.map((i) => i.label)).toEqual(["Products", "Payouts", "Billing", "Recall access"]);
  });
});

describe("the access page", () => {
  it("offers to create the product from an approved typed notice, with no operator shortcuts", () => {
    const typed: AccessRequest = { ...REQ, id: "t1", recall_id: null, typed: true, status: "approved", title: "Acme Kettle Recall", typed_notice: "Acme recalls…" };
    const html = renderToStaticMarkup(<AccessRequests status={status({ approved: true, requests: [typed] })} />);
    expect(html).toContain("Create the product");
    expect(html).not.toContain('href="/operator"');
    expect(html).not.toContain("data-approve-own");
    expect(html).toContain("Recall access"); // not the gated heading
    expect(html).toContain("checks your email domain");
  });

  it("says another company runs the recall, without the API's organization wording", () => {
    const held = { status: 409, title: "Conflict", detail: "another organization runs this recall; revoke its approval in the operator queue first", correlationId: "c", retryAfterSeconds: null };
    const html = renderToStaticMarkup(<ProblemAlert problem={held} />);
    expect(html).toContain("Another company runs this recall.");
    expect(html).not.toMatch(/organization/i);
    expect(html).not.toContain("Refresh the page");
    const decided = renderToStaticMarkup(<ProblemAlert problem={{ ...held, detail: "another organization is already approved for this recall" }} />);
    expect(decided).toContain("Another company runs this recall.");
  });
});

describe("the operator window's list", () => {
  it("shows the hints and the actions each status allows", () => {
    const decided = { ...OP, id: "a2", status: "approved", firm_name: "Primark", requester_domain: "primark.com", domain_matches: true };
    const html = renderToStaticMarkup(<OperatorQueue requests={[OP, decided]} />);
    expect(html).toContain("Waiting for review (1)");
    expect(html).toContain("which does not match Primark");
    expect(html.match(/data-operator-action="approved"/g)).toHaveLength(1);
    expect(html).toContain('data-operator-action="revoked"'); // the approved one can be revoked
  });

  it("warns when another company already runs the recall", () => {
    const html = renderToStaticMarkup(<OperatorQueue requests={[{ ...OP, approved_elsewhere: "Demo Recall Co" }]} />);
    expect(html).toContain("Demo Recall Co already runs this recall");
  });
});

describe("the alert for a company not approved yet", () => {
  it("says so and links to Recall access instead of blaming the account", () => {
    const html = renderToStaticMarkup(
      <ProblemAlert problem={{ status: 403, title: "Forbidden", detail: "This organization is not approved to run a recall yet.", correlationId: "c", retryAfterSeconds: null }} />,
    );
    expect(html).toContain("Your company is not approved to run this recall yet.");
    expect(html).toContain('href="/app/access"');
    expect(html).not.toMatch(/organization/i);
    expect(html).not.toContain("This account cannot do this.");
    const other = renderToStaticMarkup(<ProblemAlert problem={{ status: 403, title: "Forbidden", detail: "admins only", correlationId: null, retryAfterSeconds: null }} />);
    expect(other).toContain("This account cannot do this.");
    expect(other).not.toContain("administrator");
  });
});

describe("company verification rules", () => {
  it("builds request bodies and refuses an incomplete typed notice", () => {
    expect(accessBody({ recallId: "r1" })).toEqual({ recall_id: "r1" });
    expect(accessBody({ title: " Kettle ", company: "Acme", notice: "text" })).toEqual({ typed: { title: "Kettle", company: "Acme", notice: "text" } });
    expect(accessBody({ title: "Kettle", company: " ", notice: "text" })).toBeNull();
  });

  it("knows which recalls were already asked for", () => {
    expect(requestFor([REQ], "r1")?.id).toBe("a1");
    expect(requestFor([{ ...REQ, status: "rejected" }], "r1")).toBeUndefined(); // a rejection may be asked again
  });

  it("offers only the moves the API allows and words unknown statuses safely", () => {
    expect(operatorActions({ status: "requested" })).toEqual(["approved", "rejected"]);
    expect(operatorActions({ status: "approved" })).toEqual(["revoked"]);
    expect(operatorActions({ status: "rejected" })).toEqual([]);
    expect(accessStatusLabel("something-new")).toBe("Under review");
    expect(domainHint({ ...OP, requester_domain: null })).toContain("unknown");
  });
});
