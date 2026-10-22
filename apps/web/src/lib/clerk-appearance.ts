// Clerk draws its widgets with its own palette, which is light: in dark mode the account widgets in the sidebar showed
// near-black text on the dark panel (seen on the first deploy, 2026-10-21). In dark mode the widgets get the
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

/** --accent in light mode (globals.css). */
export const BRAND_PRIMARY_LIGHT = "#2672cf";

// Clerk's links and primary button. Each reads the page's own tokens through CSS variables, which the browser
// resolves per color scheme: blue #2672cf with white text in light mode, #3987e5 with near-black text in dark mode.
const ACCENT_TEXT = { color: "var(--accent)" } as const;

/**
 * The provider-wide appearance (ClerkShell): the one brand blue on every Clerk widget instead of Clerk's charcoal
 * button (white in dark mode), seen on the live sign-in and sign-up pages. The provider renders on the server, which
 * cannot know the color scheme, so colorPrimary is the light value Clerk derives its focus and hover shades from, and
 * the button and links take the per-mode tokens above. Component appearances (AccountControls) still apply on top.
 */
export const CLERK_BRAND_APPEARANCE = {
  variables: { colorPrimary: BRAND_PRIMARY_LIGHT },
  elements: {
    formButtonPrimary: {
      backgroundColor: "var(--accent)",
      color: "var(--paper)",
      // Clerk's own states shade the light hex, which would turn the dark-mode button darker on hover.
      "&:hover": { backgroundColor: "var(--accent)", opacity: 0.9 },
      "&:focus": { backgroundColor: "var(--accent)" },
      "&:active": { backgroundColor: "var(--accent)" },
    },
    footerActionLink: ACCENT_TEXT,
    formFieldAction: ACCENT_TEXT,
    formResendCodeLink: ACCENT_TEXT,
    identityPreviewEditButton: ACCENT_TEXT,
  },
} as const;

/**
 * Clerk's titles name the Clerk application ("Sign in to My Application" on the live site, whose dashboard still
 * has Clerk's default name). These name RecallOps in code; the dashboard name still reaches Clerk's emails.
 */
export const CLERK_LOCALIZATION = {
  signIn: {
    start: {
      title: "Sign in to RecallOps",
      titleCombined: "Continue to RecallOps",
      subtitle: "Welcome back. Sign in to continue.",
      subtitleCombined: "Welcome back. Sign in to continue.",
    },
    emailCode: { subtitle: "Enter the code we sent to your email to continue to RecallOps." },
    emailLink: { subtitle: "Use the link we sent to your email to continue to RecallOps." },
    phoneCode: { subtitle: "Enter the code we sent to your phone to continue to RecallOps." },
  },
  signUp: {
    start: {
      title: "Create your RecallOps account",
      titleCombined: "Create your RecallOps account",
      subtitle: "Welcome. Enter your details to get started.",
      subtitleCombined: "Welcome. Enter your details to get started.",
    },
    emailLink: { subtitle: "Use the link we sent to your email to continue to RecallOps." },
  },
} as const;
