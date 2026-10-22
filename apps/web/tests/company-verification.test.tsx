// Company verification (decisions log 2026-10-22): the first live deploy let any signed-up organization run any
// recall. Now the console shows an unapproved organization only its access requests, and the operator decides.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: () => {}, push: () => {} }), usePathname: () => "/app/programs" }));

import { gateConsole } from "@/components/AccessGate";
import { AppShell, OPERATOR_NAV } from "@/components/AppShell";
import { AccessRequests } from "@/components/AccessRequests";
import { NewProgram } from "@/components/NewProgram";
import { OperatorQueue } from "@/components/OperatorQueue";
import { ProblemAlert } from "@/components/ProblemAlert";
import type { AccessRequest, AccessStatus, OperatorAccessRequest } from "@/lib/api-types";
import { accessBody, accessStatusLabel, domainHint, operatorActions, requestFor, typedReadyForProgram } from "@/lib/access";

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

  it("shows only the access page to an organization not approved yet", async () => {
    const html = await body(status({ requests: [REQ] }));
    expect(html).not.toContain("data-console-child");
    expect(html).toContain("Request access to your recall");
    expect(html).toContain("Waiting for review");
  });

  it("opens the console for the platform operator, approved or not, and tells the sidebar", async () => {
    for (const s of [status({ operator: true }), status({ operator: true, approved: true })]) {
      const gate = await gateConsole(child, ok(s));
      expect(gate.operator).toBe(true);
      expect(renderToStaticMarkup(<>{gate.body}</>)).toContain("data-console-child");
    }
  });

  it("opens the console when approved, when not required, and when the status cannot be read", async () => {
    for (const s of [status({ approved: true }), status({ required: false })]) {
      expect(await body(s)).toContain("data-console-child");
      expect((await gateConsole(child, ok(s))).operator).toBe(false);
    }
    const down = async () => ({ ok: false as const, problem: { status: 503, title: "Down", detail: null, correlationId: null, retryAfterSeconds: null } });
    const gate = await gateConsole(child, down);
    expect(renderToStaticMarkup(<>{gate.body}</>)).toContain("data-console-child");
    expect(gate.operator).toBe(false);
  });
});

describe("the operator's sidebar link", () => {
  it("shows the queue to the operator only", () => {
    expect(renderToStaticMarkup(<AppShell identityNote="n" role="admin">x</AppShell>)).not.toContain('href="/operator"');
    expect(renderToStaticMarkup(<AppShell identityNote="n" role="admin" operator>x</AppShell>)).toContain('href="/operator"');
    const alone = renderToStaticMarkup(<AppShell identityNote="n" items={OPERATOR_NAV} operator>x</AppShell>);
    expect(alone).toContain(">Operator queue<");
    for (const label of ["Programs", "Payouts", "Billing", "Recall access"]) expect(alone).not.toContain(`>${label}<`);
    expect(OPERATOR_NAV.map((i) => i.href)).toEqual(["/operator"]);
  });
});

describe("the access page", () => {
  it("offers to create the program from an approved typed notice, and links the operator to the queue", () => {
    const typed: AccessRequest = { ...REQ, id: "t1", recall_id: null, typed: true, status: "approved", title: "Acme Kettle Recall", typed_notice: "Acme recalls…" };
    const html = renderToStaticMarkup(<AccessRequests status={status({ approved: true, operator: true, requests: [typed] })} />);
    expect(html).toContain("Create the program");
    expect(html).toContain('href="/operator"');
    expect(html).toContain("Recall access"); // not the gated heading
    expect(html).toContain("data-operator-intro"); // the operator's own requests are approved on request
  });
});

describe("the operator's own requests", () => {
  it("offers to approve an open request at once to the operator only", () => {
    const open = renderToStaticMarkup(<AccessRequests status={status({ operator: true, requests: [REQ] })} />);
    expect(open).toContain(`data-approve-own="${REQ.id}"`);
    expect(open).toContain("Approve now");
    const company = renderToStaticMarkup(<AccessRequests status={status({ requests: [REQ] })} />);
    expect(company).not.toContain("data-approve-own");
    expect(company).not.toContain("data-operator-intro");
    expect(company).toContain("checks your work email domain");
  });

  it("says another organization runs the recall, with the way out, instead of asking for a refresh", () => {
    const held = { status: 409, title: "Conflict", detail: "another organization runs this recall; revoke its approval in the operator queue first", correlationId: "c", retryAfterSeconds: null };
    const html = renderToStaticMarkup(<ProblemAlert problem={held} />);
    expect(html).toContain("Another organization runs this recall.");
    expect(html).toContain('href="/operator"');
    expect(html).not.toContain("Refresh the page");
    const decided = renderToStaticMarkup(<ProblemAlert problem={{ ...held, detail: "another organization is already approved for this recall" }} />);
    expect(decided).toContain("Another organization runs this recall.");
  });

  it("sends a typed notice through Recall access while approval is required", () => {
    const gated = renderToStaticMarkup(<NewProgram existingRecallIds={[]} typedNeedsApproval />);
    expect(gated).toContain("data-typed-via-access");
    expect(gated).toContain('href="/app/access"');
    expect(gated).not.toContain("Enter the notice by hand");
    expect(renderToStaticMarkup(<NewProgram existingRecallIds={[]} />)).toContain("Enter the notice by hand");
  });
});

describe("the operator queue", () => {
  it("shows the hints and the actions each status allows", () => {
    const decided = { ...OP, id: "a2", status: "approved", firm_name: "Primark", requester_domain: "primark.com", domain_matches: true };
    const html = renderToStaticMarkup(<OperatorQueue requests={[OP, decided]} />);
    expect(html).toContain("Waiting for your review (1)");
    expect(html).toContain("which does not match Primark");
    expect(html.match(/data-operator-action="approved"/g)).toHaveLength(1);
    expect(html).toContain('data-operator-action="revoked"'); // the approved one can be revoked
  });

  it("warns when another organization already runs the recall", () => {
    const html = renderToStaticMarkup(<OperatorQueue requests={[{ ...OP, approved_elsewhere: "Demo Recall Co" }]} />);
    expect(html).toContain("Demo Recall Co already runs this recall");
  });
});

describe("the alert for an organization not approved yet", () => {
  it("says so and links to Recall access instead of blaming the user's role", () => {
    const html = renderToStaticMarkup(
      <ProblemAlert problem={{ status: 403, title: "Forbidden", detail: "This organization is not approved to run a recall yet.", correlationId: "c", retryAfterSeconds: null }} />,
    );
    expect(html).toContain("This organization is not approved to run this recall yet.");
    expect(html).toContain('href="/app/access"');
    expect(html).not.toContain("Your role does not allow");
    const role = renderToStaticMarkup(<ProblemAlert problem={{ status: 403, title: "Forbidden", detail: "admins only", correlationId: null, retryAfterSeconds: null }} />);
    expect(role).toContain("Your role does not allow");
  });
});

describe("company verification rules", () => {
  it("builds request bodies and refuses an incomplete typed notice", () => {
    expect(accessBody({ recallId: "r1" })).toEqual({ recall_id: "r1" });
    expect(accessBody({ title: " Kettle ", company: "Acme", notice: "text" })).toEqual({ typed: { title: "Kettle", company: "Acme", notice: "text" } });
    expect(accessBody({ title: "Kettle", company: " ", notice: "text" })).toBeNull();
  });

  it("knows which recalls were already asked for and which typed notices are ready", () => {
    expect(requestFor([REQ], "r1")?.id).toBe("a1");
    expect(requestFor([{ ...REQ, status: "rejected" }], "r1")).toBeUndefined(); // a rejection may be asked again
    expect(typedReadyForProgram({ ...REQ, typed: true, status: "approved" })).toBe(true);
    expect(typedReadyForProgram({ ...REQ, typed: true, status: "approved", program_id: "p1" })).toBe(false);
  });

  it("offers only the moves the API allows and words unknown statuses safely", () => {
    expect(operatorActions({ status: "requested" })).toEqual(["approved", "rejected"]);
    expect(operatorActions({ status: "approved" })).toEqual(["revoked"]);
    expect(operatorActions({ status: "rejected" })).toEqual([]);
    expect(accessStatusLabel("something-new")).toBe("Under review");
    expect(domainHint({ ...OP, requester_domain: null })).toContain("unknown");
  });
});
