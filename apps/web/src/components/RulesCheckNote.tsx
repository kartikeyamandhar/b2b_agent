"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { browserStore, dismissRulesNote, rulesNoteDismissed } from "@/lib/products";

/** The note itself, without the memory (rendered as is by tests and by RulesCheckReminder). */
export function RulesCheckNote({ programId, onDismiss }: { programId: string; onDismiss: () => void }) {
  return (
    <div className="flex flex-wrap items-start gap-3 rounded-lg border border-brand/40 border-l-4 border-l-brand bg-brand-soft px-4 py-3 text-sm" data-rules-note>
      <p className="min-w-0 flex-1">
        The rules were read from the recall notice automatically. <Link href={`/app/programs/${programId}/settings`}>Check them</Link>.
      </p>
      <button type="button" onClick={onDismiss} className="shrink-0 rounded-md border border-line-strong bg-surface px-2.5 py-1 text-xs font-medium text-ink hover:bg-panel" data-rules-note-dismiss>
        Dismiss
      </button>
    </div>
  );
}

/**
 * The note on an open product, until this browser dismisses it for this product. Storage is read after mounting, so
 * the server and the first client render agree (no note), and the note never flashes for someone who dismissed it.
 */
export function RulesCheckReminder({ programId }: { programId: string }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    setShown(!rulesNoteDismissed(browserStore(), programId));
  }, [programId]);
  if (!shown) return null;
  return (
    <RulesCheckNote
      programId={programId}
      onDismiss={() => {
        dismissRulesNote(browserStore(), programId);
        setShown(false);
      }}
    />
  );
}
