// The product page (plan v3, owner decision 2026-10-22): each product has one public claim link (intake link) that
// anyone can use to start a claim, shown with its QR code and a printable poster. This file decides what the page
// says about the link, builds the poster's facts, and remembers the dismissed "check the rules" note per product.
// Pure apart from the storage it is handed, so it is unit-tested.
import type { Program } from "@/lib/api-types";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Where the product's claim link stands. Only "live" has a link: the API sends intake_url only while the product
 *  takes new claims. */
export type LinkState =
  | { kind: "live"; url: string }
  /** Open, but the plan covers another product (Free runs one at a time). Claims already started carry on. */
  | { kind: "paused" }
  /** A refund product whose notice has no price: it stays a draft until the price range is set. */
  | { kind: "needs_amount" }
  | { kind: "draft" }
  | { kind: "closed" }
  /** Anything else (an older API, or a status added later): no link to show. */
  | { kind: "none" };

export function linkState(p: Pick<Program, "status" | "intake_url" | "takes_new_claims" | "needs_refund_amount">): LinkState {
  if (p.status === "closed") return { kind: "closed" };
  if (p.status === "draft") return p.needs_refund_amount ? { kind: "needs_amount" } : { kind: "draft" };
  if (p.status === "open" && p.takes_new_claims === false) return { kind: "paused" };
  if (p.status === "open" && p.intake_url) return { kind: "live", url: p.intake_url };
  return { kind: "none" };
}

/** A short marker for the products list, or null when the product needs nothing. */
export function productNote(p: Pick<Program, "status" | "intake_url" | "takes_new_claims" | "needs_refund_amount">): string | null {
  const s = linkState(p);
  if (s.kind === "paused") return "Paused: takes no new claims";
  if (s.kind === "needs_amount") return "Set the refund amount";
  return null;
}

/** POST /v1/programs/{id}/intake/replace, only for an id that can name a product. */
export function intakeReplacePath(programId: string): string | null {
  return UUID.test(programId) ? `/v1/programs/${programId}/intake/replace` : null;
}

/** How many model numbers the poster lists before "and N more" (a poster is read from across a room). */
export const POSTER_MODELS_MAX = 8;

export type Poster = {
  company: string | null;
  title: string;
  url: string;
  models: string[];
  /** Models left off the poster. */
  moreModels: number;
};

/** The poster's facts: the company (when known), the product, its link, and its first models. */
export function posterFor(p: Pick<Program, "title" | "identification_rules">, url: string, company: string | null | undefined, max = POSTER_MODELS_MAX): Poster {
  const models = [...new Set((p.identification_rules.models ?? []).map((m) => m.trim()).filter(Boolean))];
  return {
    company: company?.trim() ? company.trim() : null,
    title: p.title,
    url,
    models: models.slice(0, max),
    moreModels: Math.max(0, models.length - max),
  };
}

/** A file name for the product's QR downloads, from its title ("acme-kettle-claim-link-qr"). */
export function qrFileName(title: string, programId: string): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40)
    .replace(/-+$/, "");
  return `${slug || `product-${programId.slice(0, 8)}`}-claim-link-qr`;
}

// The note that the rules were read from the notice automatically. Dismissing it is a per-viewer convenience kept in
// this browser only; storage can be missing or throw (private windows, blocked site data), and then the note shows.
export const RULES_NOTE_PREFIX = "recallops.rules-note-dismissed.";

export type NoteStore = Pick<Storage, "getItem" | "setItem">;

/** This browser's localStorage, or null when it cannot be used. */
export function browserStore(): NoteStore | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function rulesNoteDismissed(store: NoteStore | null, programId: string): boolean {
  try {
    return store?.getItem(RULES_NOTE_PREFIX + programId) === "1";
  } catch {
    return false;
  }
}

export function dismissRulesNote(store: NoteStore | null, programId: string): void {
  try {
    store?.setItem(RULES_NOTE_PREFIX + programId, "1");
  } catch {
    // Not remembered: the note shows again next time, which is harmless.
  }
}
