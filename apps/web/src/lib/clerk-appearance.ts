// Clerk draws its widgets with its own palette, which is light: in dark mode the organization switcher in the sidebar
// showed near-black text on the dark panel (seen on the first deploy, 2026-10-21). In dark mode the widgets get the
// dark tokens from globals.css as plain colors (the pinned Clerk types do not promise CSS-variable support). Keep
// these in step with the dark block in globals.css.
export const CLERK_DARK_VARIABLES = {
  colorForeground: "#f0efec", // --ink
  colorMutedForeground: "#c3c2b7", // --ink-muted
  colorBackground: "#1a1a19", // --surface
  colorInput: "#1a1a19", // --surface
  colorInputForeground: "#f0efec", // --ink
  colorNeutral: "#f0efec", // --ink: Clerk derives borders and hovers from it
  colorPrimary: "#3987e5", // --brand
  colorPrimaryForeground: "#111110", // --paper (5.2:1 on the brand blue)
} as const;

/** Appearance for Clerk widgets: the dark palette in dark mode, Clerk's defaults (already light) otherwise. */
export function clerkAppearance(dark: boolean): { variables: typeof CLERK_DARK_VARIABLES } | undefined {
  return dark ? { variables: CLERK_DARK_VARIABLES } : undefined;
}
