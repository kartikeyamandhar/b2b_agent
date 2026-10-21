// Reissuing one claim's portal link (POST /v1/claims/{id}/link, admin). The new link replaces the old one at once,
// so the old link stops working; the backend refuses a paid or closed claim and any claim of a closed program.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Claim statuses the backend refuses (programs.py REISSUE_REFUSED): nothing is left for the claimant to do. */
export const REISSUE_REFUSED = ["paid", "closed"] as const;

export type ReissueAvailability = { allowed: true } | { allowed: false; reason: string };

export function reissueAvailability(status: string, canAdmin: boolean, programStatus: string | null = null): ReissueAvailability {
  if (!canAdmin) return { allowed: false, reason: "Only an admin can issue a new claim link." };
  if (programStatus === "closed") return { allowed: false, reason: "The program is closed: its claim links no longer work and none can be issued." };
  if ((REISSUE_REFUSED as readonly string[]).includes(status)) {
    return { allowed: false, reason: `The claim is ${status === "paid" ? "paid" : "closed"}: the claimant has nothing left to do, so no new link is needed.` };
  }
  return { allowed: true };
}

export function reissuePath(claimId: string): string | null {
  return UUID.test(claimId) ? `/v1/claims/${claimId}/link` : null;
}
