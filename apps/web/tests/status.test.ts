import { describe, expect, it } from "vitest";

import * as api from "@/lib/api-types";
import { statusLabel, type StatusKind } from "@/lib/status";

const CONTRACT: Array<[StatusKind, readonly string[]]> = [
  ["claim", api.ClaimDetailStatusValues],
  ["claim", api.ClaimSummaryStatusValues],
  ["claim", api.PortalViewStatusValues],
  ["run", api.RunSummaryStatusValues],
  ["payout", api.PayoutViewStatusValues],
  ["payout", api.PortalViewPayoutStatusValues],
  ["outcome", api.DecisionOutcomeValues],
  ["outcome", api.ClaimSummaryOutcomeValues],
];

describe("statusLabel", () => {
  it.each(CONTRACT)("labels every %s value in the contract", (kind, values) => {
    for (const v of values) expect(statusLabel(kind, v).known, `${kind}:${v}`).toBe(true);
  });

  it("shows an unknown value as in progress", () => {
    expect(statusLabel("payout", "teleporting")).toEqual({ label: "In progress", tone: "progress", known: false });
  });

  it("does not treat prototype keys as statuses", () => {
    expect(statusLabel("claim", "constructor").known).toBe(false);
  });

  it("uses the agreed wording for held payouts and issued links", () => {
    expect(statusLabel("payout", "held").label).toBe("On hold, a person is reviewing");
    expect(statusLabel("claim", "draft").label).toBe("Link issued");
    expect(statusLabel("run", "waiting_human").tone).toBe("attention");
  });

  it("renders a missing value as None", () => {
    expect(statusLabel("payout", null).label).toBe("None");
  });
});
