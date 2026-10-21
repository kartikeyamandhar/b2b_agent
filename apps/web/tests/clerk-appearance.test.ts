// The Clerk widgets in the sidebar must stay legible in dark mode (first deploy, 2026-10-21: the organization name was
// near-black on the dark panel).
import { describe, expect, it } from "vitest";

import { CLERK_DARK_VARIABLES, clerkAppearance } from "@/lib/clerk-appearance";

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

describe("Clerk widget appearance", () => {
  it("keeps Clerk's light defaults in light mode", () => {
    expect(clerkAppearance(false)).toBeUndefined();
  });

  it("uses the dark tokens in dark mode, with readable text on the sidebar and on Clerk's own popovers", () => {
    const v = clerkAppearance(true)!.variables;
    expect(v).toBe(CLERK_DARK_VARIABLES);
    const panel = "#161615"; // --panel, the sidebar behind the switcher
    expect(contrast(v.colorForeground, panel)).toBeGreaterThanOrEqual(7);
    expect(contrast(v.colorMutedForeground, v.colorBackground)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(v.colorPrimaryForeground, v.colorPrimary)).toBeGreaterThanOrEqual(4.5);
  });
});
