import { describe, expect, it } from "vitest";

import * as api from "@/lib/api-types";
import { formatCents, formatCount, formatDate, formatPercent } from "@/lib/format";
import { CLAIM_STATUS_ORDER, programMetrics, remedyLabel, statusRows } from "@/lib/programs";

describe("programMetrics", () => {
  const counts = { draft: 3, approved: 72, denied: 94, needs_info: 107, flagged: 117, under_review: 4, capped: 2, paid: 110, closed: 6 };

  it("splits issued links from filed claims and counts review and corrections", () => {
    expect(programMetrics({ claim_counts: counts, units_affected: 1000 })).toEqual({
      notSubmitted: 3,
      filed: 512,
      awaitingReview: 123,
      needsInfo: 107,
      corrected: 116,
      correctionRate: 0.116,
    });
  });

  it("treats missing statuses as zero and unknown units as no rate", () => {
    expect(programMetrics({ claim_counts: {}, units_affected: null })).toEqual({
      notSubmitted: 0,
      filed: 0,
      awaitingReview: 0,
      needsInfo: 0,
      corrected: 0,
      correctionRate: null,
    });
    expect(programMetrics({ claim_counts: { paid: 1 }, units_affected: 0 }).correctionRate).toBeNull();
  });

  it("counts a status the backend adds later as filed", () => {
    expect(programMetrics({ claim_counts: { archived: 5, draft: 1 }, units_affected: 10 }).filed).toBe(5);
  });

  it("ignores negative or non-numeric counts", () => {
    const bad = { approved: -4, paid: Number.NaN, denied: 2 } as Record<string, number>;
    expect(programMetrics({ claim_counts: bad, units_affected: 10 }).filed).toBe(2);
  });
});

describe("statusRows", () => {
  it("lists every contract claim status once, zeros included", () => {
    expect([...CLAIM_STATUS_ORDER].sort()).toEqual([...api.ClaimDetailStatusValues].sort());
    const rows = statusRows({ approved: 3, denied: 1 });
    expect(rows.map((r) => r.status)).toEqual([...CLAIM_STATUS_ORDER]);
    expect(rows.find((r) => r.status === "flagged")).toEqual({ status: "flagged", count: 0, shareOfFiled: 0 });
    expect(rows.find((r) => r.status === "approved")?.shareOfFiled).toBe(0.75);
  });

  it("gives issued links no share and appends unknown statuses", () => {
    const rows = statusRows({ draft: 9, approved: 1, zeta: 1, alpha: 2 });
    expect(rows.find((r) => r.status === "draft")?.shareOfFiled).toBeNull();
    expect(rows.slice(-2).map((r) => r.status)).toEqual(["alpha", "zeta"]);
  });

  it("has no share when nothing is filed", () => {
    expect(statusRows({}).every((r) => r.shareOfFiled === null)).toBe(true);
  });
});

describe("remedyLabel", () => {
  it.each([
    ["refund", "Refund"],
    ["repair", "Repair"],
    ["replacement", "Replacement"],
    ["multiple", "More than one remedy"],
    [null, "Not stated in the notice"],
  ])("labels %s", (value, label) => {
    expect(remedyLabel(value)).toBe(label);
  });

  it("covers every remedy type in the contract", () => {
    for (const v of api.RemedyRulesRemedyTypeValues) expect(remedyLabel(v)).not.toBe(v);
  });
});

describe("format", () => {
  it("formats counts, cents, dates and percents", () => {
    expect(formatCount(53634)).toBe("53,634");
    expect(formatCount(null)).toBe("—");
    expect(formatCents(10000)).toBe("$100.00");
    expect(formatCents(5)).toBe("$0.05");
    expect(formatDate("2026-10-16T22:40:00Z")).toBe("Oct 16, 2026");
    expect(formatDate("2024-06-01")).toBe("Jun 1, 2024");
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not a date")).toBe("not a date");
    expect(formatPercent(0.116)).toBe("11.6%");
    expect(formatPercent(null)).toBe("—");
  });
});
