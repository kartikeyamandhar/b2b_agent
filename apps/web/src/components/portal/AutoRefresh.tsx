"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef } from "react";

import { STATUS_REFRESH_MS, shouldAutoRefresh } from "@/lib/portal";

/**
 * Re-renders the server status page a few times while the claim is being checked. Capped (STATUS_REFRESH_MAX) and
 * paused while the tab is hidden, because each refresh spends one of the link's 60 requests per hour.
 */
export function AutoRefresh({ status }: { status: string }) {
  const router = useRouter();
  const count = useRef(0);
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "hidden") return;
      if (!shouldAutoRefresh(status, count.current)) {
        window.clearInterval(timer);
        return;
      }
      count.current += 1;
      router.refresh();
    }, STATUS_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [router, status]);
  return (
    <p className="text-sm text-ink-muted" aria-hidden="true">
      This page checks for updates every {STATUS_REFRESH_MS / 1000} seconds for a few minutes.
    </p>
  );
}
