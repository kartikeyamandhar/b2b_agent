// Phase F: console keyboard navigation (02-ui.md section 6).
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import { ClaimsTable } from "@/components/ClaimsTable";
import { ReviewerActions } from "@/components/ReviewerActions";
import { ClaimDetail, ClaimPage } from "@/lib/api-types";
import { actionForKey, moveIndex, queueMove, shortcutKey } from "@/lib/shortcuts";

const page = ClaimPage.parse(JSON.parse(readFileSync(new URL("./fixtures/claims-page.json", import.meta.url), "utf8")));
const claim = ClaimDetail.parse(JSON.parse(readFileSync(new URL("./fixtures/claim-detail.json", import.meta.url), "utf8")));

describe("shortcut rules", () => {
  it("ignores keys while typing or with a modifier", () => {
    expect(shortcutKey({ key: "j" })).toBe("j");
    expect(shortcutKey({ key: "j", target: { tagName: "BODY" } })).toBe("j");
    for (const tagName of ["INPUT", "TEXTAREA", "SELECT", "input"]) expect(shortcutKey({ key: "a", target: { tagName } })).toBeNull();
    expect(shortcutKey({ key: "a", target: { tagName: "DIV", isContentEditable: true } })).toBeNull();
    for (const mod of ["ctrlKey", "metaKey", "altKey"] as const) expect(shortcutKey({ key: "d", [mod]: true })).toBeNull();
    expect(shortcutKey({ key: "a", target: null })).toBe("a");
  });

  it("maps queue keys and stays inside the page", () => {
    expect([queueMove("j"), queueMove("k"), queueMove("Enter"), queueMove("x"), queueMove(null)]).toEqual(["next", "prev", "open", null, null]);
    expect(moveIndex(null, "next", 3)).toBe(0);
    expect(moveIndex(null, "prev", 3)).toBe(2);
    expect(moveIndex(2, "next", 3)).toBe(2);
    expect(moveIndex(0, "prev", 3)).toBe(0);
    expect(moveIndex(1, "next", 0)).toBeNull();
  });

  it("maps a, d, i to approve, deny, request info, and nothing else", () => {
    expect([actionForKey("a"), actionForKey("d"), actionForKey("i")]).toEqual(["approve", "deny", "request_info"]);
    for (const k of ["f", "A", "Enter", null, "constructor"]) expect(actionForKey(k)).toBeNull();
  });
});

describe("components", () => {
  it("marks the selected claim in the table and the phone cards", () => {
    const id = page.items[1]!.id;
    const html = renderToStaticMarkup(<ClaimsTable claims={page.items} selectedId={id} />);
    expect(html.match(/data-selected="true"/g)).toHaveLength(2);
    expect(html).toContain('aria-current="true"');
    expect(renderToStaticMarkup(<ClaimsTable claims={page.items} />)).not.toContain("data-selected");
  });

  it("tells a reviewer the action keys, and that each still asks to confirm", () => {
    const flagged = { ...claim, status: "flagged" } as ClaimDetail;
    const html = renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <ReviewerActions claim={flagged} onDone={() => {}} />
      </QueryClientProvider>,
    );
    expect(html).toContain("data-shortcut-hint");
    expect(html).toContain("Each still asks you to confirm");
  });
});
