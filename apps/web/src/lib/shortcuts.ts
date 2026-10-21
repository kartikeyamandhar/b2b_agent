// Console keyboard shortcuts (02-ui.md section 6): j and k move through the queue, Enter opens the claim; on a claim,
// a, d, and i open the approve, deny, and request-info confirmation (never act without it). Shortcuts are ignored
// while typing in a field or with a modifier held, so they never steal text input or browser shortcuts.
import type { ActionKind } from "@/lib/claim-detail";

export type KeyLike = {
  key: string;
  altKey?: boolean;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  /** An event target; only tagName and isContentEditable are read. */
  target?: unknown;
};

type TargetLike = { tagName?: unknown; isContentEditable?: unknown };

const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** The key a shortcut should act on, or null when the person is typing or using a modifier. */
export function shortcutKey(e: KeyLike): string | null {
  if (e.altKey || e.ctrlKey || e.metaKey) return null;
  const t = (typeof e.target === "object" && e.target !== null ? e.target : null) as TargetLike | null;
  if (t && (t.isContentEditable === true || (typeof t.tagName === "string" && TYPING.has(t.tagName.toUpperCase())))) return null;
  return e.key;
}

export type QueueMove = "next" | "prev" | "open";

export function queueMove(key: string | null): QueueMove | null {
  if (key === "j") return "next";
  if (key === "k") return "prev";
  if (key === "Enter") return "open";
  return null;
}

/** Moves within the list; the first j selects the first claim. */
export function moveIndex(current: number | null, move: "next" | "prev", length: number): number | null {
  if (length === 0) return null;
  if (current === null) return move === "next" ? 0 : length - 1;
  return move === "next" ? Math.min(current + 1, length - 1) : Math.max(current - 1, 0);
}

export const ACTION_KEYS: Record<string, ActionKind> = { a: "approve", d: "deny", i: "request_info" };

export function actionForKey(key: string | null): ActionKind | null {
  return key && Object.hasOwn(ACTION_KEYS, key) ? ACTION_KEYS[key]! : null;
}
