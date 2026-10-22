// Visual and copy fixes on the public pages and the claim portal, from the 2026-10-21 screenshot audit: the claimant's
// question showed reason codes, the photo step quoted a URL tail as an instruction, sign-in read "My Application" and
// carried no brand, unknown URLs got Next's black 404, and claimants saw "Error 0".
import { readFileSync } from "node:fs";

import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const clerk = vi.hoisted(() => ({ providerProps: null as null | Record<string, unknown> }));

// The real @clerk/nextjs cannot load outside Next; the mock records what the provider was given.
vi.mock("@clerk/nextjs", () => ({
  ClerkProvider: ({ children, ...props }: { children: ReactNode } & Record<string, unknown>) => {
    clerk.providerProps = props;
    return <div data-clerk-provider>{children}</div>;
  },
  SignIn: () => <div data-sign-in-widget />,
  SignUp: () => <div data-sign-up-widget />,
  OrganizationList: () => <div data-org-list />,
}));
vi.mock("@/components/OperatorLink", () => ({ OperatorLink: () => null }));
vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

import ClaimFormPage from "@/app/claim/[token]/page";
import PortalLayout from "@/app/claim/layout";
import NotFound from "@/app/not-found";
import Home from "@/app/page";
import SignInPage from "@/app/sign-in/[[...sign-in]]/page";
import SignUpPage from "@/app/sign-up/[[...sign-up]]/page";
import { AuthOff } from "@/components/AuthOff";
import { NeedsInfoReply } from "@/components/portal/NeedsInfoReply";
import { ClaimStepper } from "@/components/portal/ClaimStepper";
import { ConnectReturn, ConnectReturnCard } from "@/components/portal/ConnectReturn";
import { PhotoCapture } from "@/components/portal/PhotoCapture";
import { StatusView } from "@/components/portal/StatusView";
import { PortalView } from "@/lib/api-types";
import { BRAND_PRIMARY_LIGHT, CLERK_BRAND_APPEARANCE, CLERK_LOCALIZATION } from "@/lib/clerk-appearance";
import {
  GENERIC_QUESTION,
  claimantQuestion,
  instructionReadsAsSentence,
  localDateTime,
  photoButtonLabel,
  photoHint,
  photoSummary,
} from "@/lib/portal";

const base = PortalView.parse(JSON.parse(readFileSync(new URL("./fixtures/portal-view.json", import.meta.url), "utf8")));
const T = "dev.00000000-0000-4000-8000-00000000d001.66dcc492-086f-5b59-b441-cbe001eb2e0b";
const status = (over: Partial<PortalView>) => renderToStaticMarkup(<StatusView token={T} view={{ ...base, ...over }} />);
const src = (path: string) => readFileSync(new URL(`../src/${path}`, import.meta.url), "utf8");

// The value the seeded 3M program carries (regex baseline, cut at the period in "scotchbrand.com").
const URL_TAIL =
  "com/3M/en_US/scotch-brand/tl1302Z-laminator-recall/, confirm that their laminator is included in the recall and upload a photograph of the laminator with the unplugged power cord cut";
const CODED_QUESTION = "To finish your claim, please provide: retailer not_listed; proof of purchase needed.";

const CLERK_ENV = { RECALLOPS_WEB_AUTH: "clerk", NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: "pk_test_placeholder", CLERK_SECRET_KEY: "sk_test_placeholder" };

beforeEach(() => {
  clerk.providerProps = null;
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("the claimant's question", () => {
  it("never shows a reason code, and keeps a worded question as written", () => {
    expect(claimantQuestion(CODED_QUESTION)).toBe(GENERIC_QUESTION);
    expect(claimantQuestion("destruction photo needed: destruction_photo duplicates the label photo")).toBe(GENERIC_QUESTION);
    expect(claimantQuestion(null)).toBe(GENERIC_QUESTION);
    expect(claimantQuestion("  ")).toBe(GENERIC_QUESTION);
    expect(claimantQuestion("Where did you buy it?")).toBe("Where did you buy it?");
  });

  it("is guarded in the question box and in the firm's thread messages, not in the claimant's own", () => {
    const out = status({
      status: "needs_info",
      pending_question: CODED_QUESTION,
      messages: [
        { sender: "firm", text: CODED_QUESTION, at: "2026-10-21T20:55:00Z" },
        { sender: "claimant", text: "my order_id is 42", at: "2026-10-21T21:00:00Z" },
      ],
    });
    expect(out).not.toContain("not_listed");
    expect(out).toContain(GENERIC_QUESTION);
    expect(out).toContain("my order_id is 42");
  });
});

describe("message times", () => {
  it("are local to the reader once the page loads, and a plain date in the server HTML", () => {
    expect(localDateTime("2026-10-22T03:55:00Z", { locale: "en-US", timeZone: "America/Los_Angeles" })).toMatch(/^Oct 21, 2026, 8:55\sPM$/u);
    expect(localDateTime("not a date")).toBe("not a date");
    const out = status({ status: "submitted", messages: [{ sender: "firm", text: "Thanks.", at: "2026-10-21T20:55:00Z" }] });
    expect(out).toContain('<time dateTime="2026-10-21T20:55:00Z">Oct 21, 2026</time>');
    expect(out).not.toContain("UTC");
    expect(out).not.toMatch(/>[^<]*20:55/); // no 24-hour time in the text (the datetime attribute keeps the instant)
  });
});

describe("the destruction photo hint", () => {
  const program = { ...base.program };

  it("never quotes a URL tail as the first thing to do", () => {
    const hint = photoHint("destruction", { ...program, destruction_instruction: URL_TAIL });
    expect(hint).toBe("Photograph the product after disabling it as the recall notice instructs.");
    expect(hint).not.toContain("com/3M");
  });

  it("keeps a short imperative as the step, and quotes a whole notice sentence as the notice's words", () => {
    expect(photoHint("destruction", { ...program, destruction_instruction: "cut the power cord." })).toBe(
      "First do this: cut the power cord. Then photograph the product so the result is visible.",
    );
    const quoted = photoHint("destruction", { ...program, destruction_instruction: "Consumers should cut the power cord and write RECALLED on the laminator" });
    expect(quoted).toContain("The notice says: “Consumers should cut the power cord and write RECALLED on the laminator.”");
    expect(quoted).not.toContain("First do this");
  });

  it("refuses fragments the extractor can leave", () => {
    for (const bad of [URL_TAIL, 'destroy the stool by writing "', "write “RECALLED", "see www.example.com for steps", "cut", "com, then cut the cord", "visit example.com and cut the cord"]) {
      expect(instructionReadsAsSentence(bad), bad).toBe(false);
    }
    for (const ok of ["cut the cord", "writing in permanent marker “Recalled” on the product", 'write "RECALLED" on the tray']) {
      expect(instructionReadsAsSentence(ok), ok).toBe(true);
    }
  });

  it("shows the guarded hint on the claim form's photo step source", () => {
    expect(src("components/portal/ClaimStepper.tsx")).toContain("hint={photoHint(kind, program)}");
  });
});

describe("photo labels", () => {
  it("names the reply form's photo buttons for what they add", () => {
    expect(photoButtonLabel("other")).toBe("Additional photo"); // the button and its legend use one name (verify pass)
    expect(photoButtonLabel("receipt")).toBe("Receipt photo");
    const html = renderToStaticMarkup(<NeedsInfoReply token="t" view={{ program: base.program, answer_fields: [], answer_photos: ["receipt"] }} />);
    expect(html).toContain(">Receipt photo</button>");
    expect(html).toContain(">Additional photo</button>");
    expect(html).not.toMatch(/Extra/);
  });

  it("titles a capture of another photo in words", () => {
    const html = renderToStaticMarkup(<PhotoCapture token={T} kind="other" hint="" alreadyUploaded onUploaded={() => {}} />);
    expect(html).toContain("Additional photo</legend>");
    expect(html).toContain("Additional photo saved.");
  });

  it("summarizes the review step's photos in sentence case", () => {
    expect(photoSummary(["label", "destruction", "receipt"], ["label", "destruction", "receipt"])).toBe("Label, destruction, and receipt photos added");
    expect(photoSummary(["label", "destruction", "receipt"], ["label"])).toBe("Label photo added; destruction and receipt photos missing");
    expect(photoSummary(["label"], [])).toBe("Label photo missing");
    expect(photoSummary([], [])).toBe("No photos needed");
  });

  it("uses that summary on the review step, with no check marks", () => {
    const stepper = src("components/portal/ClaimStepper.tsx");
    // photoKinds is the program's photos with the label photo always first (plan v3).
    expect(stepper).toContain('["Photos", photoSummary(photoKinds, uploaded)]');
    expect(stepper).not.toContain("✓");
  });
});

describe("the reply form layout", () => {
  const html = renderToStaticMarkup(<NeedsInfoReply token="t" view={{ program: base.program, answer_fields: [{ name: "retailer_claimed", value: "Walmart" }], answer_photos: [] }} />);

  it("spaces each legend from its first field and sets it apart as a group heading", () => {
    expect(html).toContain('<legend class="mb-2 text-base font-semibold">Check these details</legend>');
    expect(html).toContain('<legend class="mb-2 text-base font-semibold">Add a photo</legend>');
  });

  it("gives field labels the claim form's weight without bolding what the claimant types", () => {
    expect(html).toContain('<span class="font-medium">Where you bought it</span>');
    expect(html).toMatch(/<label class="grid gap-1 text-base"><span/);
    expect(html).toContain('A note for the company <span class="font-normal text-ink-muted">(optional)</span>');
  });
});

describe("portal errors", () => {
  it("show claimants a reference, never an error code", async () => {
    vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
    vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
    vi.stubGlobal("fetch", async () => Response.json({ title: "Not Found", status: 404, detail: "claim not found", correlation_id: "c-404" }, { status: 404 }));
    const out = renderToStaticMarkup(await ClaimFormPage({ params: Promise.resolve({ token: T }) }));
    expect(out).toContain("This claim link is not valid or has expired");
    expect(out).toContain("Reference <code");
    expect(out).toContain("c-404");
    expect(out).not.toContain("Error 404");
  });

  it("pass the claimant audience from every portal component that can fail", () => {
    for (const f of ["ClaimStepper", "NeedsInfoReply", "PayoutChoice", "PhotoCapture"]) {
      const s = src(`components/portal/${f}.tsx`);
      expect(s.match(/<ProblemAlert /g)?.length, f).toBe(s.match(/audience="claimant"/g)?.length);
    }
    for (const page of ["app/claim/[token]/page.tsx", "app/claim/[token]/status/page.tsx"]) expect(src(page), page).toContain('audience="claimant"');
  });

  it("offer Try again styled like Retake beside it, visible on the dark surface", () => {
    const s = src("components/portal/PhotoCapture.tsx");
    const start = s.indexOf("<button", s.indexOf('phase === "error" && blob.current'));
    const retry = s.slice(start, s.indexOf("Try again", start));
    expect(retry).toContain("rounded-md border border-line-strong bg-surface px-4 text-base font-medium text-ink hover:border-ink");
    expect(retry).not.toContain("border-line ");
  });
});

describe("portal frame and Stripe return pages", () => {
  it("puts the header's padding inside the content width, so the brand lines up with the column", () => {
    const html = renderToStaticMarkup(<PortalLayout>x</PortalLayout>);
    expect(html).toContain('<header class="border-b border-line bg-surface">');
    expect(html).toMatch(/<p class="mx-auto flex max-w-xl items-center gap-2 px-4 py-3\.5 /);
  });

  it("gives the Stripe return and refresh pages a heading card in both moments", () => {
    const pending = renderToStaticMarkup(<ConnectReturn kind="return" />);
    expect(pending).toContain("You are back from Stripe</h1>");
    expect(pending).toContain("Taking you back to your claim");
    const lost = renderToStaticMarkup(<ConnectReturnCard kind="refresh" lost />);
    expect(lost).toContain('class="rounded-xl border border-line bg-surface p-5"');
    expect(lost).toContain("Your Stripe setup link expired</h1>");
    expect(lost).toContain("get a new setup link and see your refund status");
    expect(renderToStaticMarkup(<ConnectReturnCard kind="return" lost />)).toContain("to see your refund status.");
  });
});

// Plan v3 has no organizations, so only the sign-in and sign-up pages carry the frame here.
describe("sign-in and sign-up pages", () => {
  it("carry the RecallOps mark and name, linking home, above the Clerk widget", () => {
    for (const [k, v] of Object.entries(CLERK_ENV)) vi.stubEnv(k, v);
    for (const [page, widget] of [
      [SignInPage(), "data-sign-in-widget"],
      [SignUpPage(), "data-sign-up-widget"],
    ] as const) {
      const html = renderToStaticMarkup(page);
      const brand = html.indexOf("data-auth-brand");
      expect(brand, widget).toBeGreaterThan(-1);
      expect(html.slice(html.lastIndexOf("<a ", brand), html.indexOf(">", brand))).toContain('href="/"');
      expect(html.indexOf(">RecallOps</span>")).toBeLessThan(html.indexOf(widget));
      // Outside main, so the global main-link underline never reaches the brand.
      expect(html.indexOf("data-auth-brand")).toBeLessThan(html.indexOf("<main"));
    }
  });

  it("give Clerk the brand blue and titles that name RecallOps", () => {
    for (const [k, v] of Object.entries(CLERK_ENV)) vi.stubEnv(k, v);
    renderToStaticMarkup(SignInPage());
    expect(clerk.providerProps?.appearance).toBe(CLERK_BRAND_APPEARANCE);
    expect(clerk.providerProps?.localization).toBe(CLERK_LOCALIZATION);
    expect(CLERK_BRAND_APPEARANCE.variables.colorPrimary).toBe(BRAND_PRIMARY_LIGHT);
    expect(CLERK_BRAND_APPEARANCE.elements.formButtonPrimary.backgroundColor).toBe("var(--accent)");
    expect(CLERK_BRAND_APPEARANCE.elements.formButtonPrimary.color).toBe("var(--paper)");
    expect(CLERK_LOCALIZATION.signIn.start.title).toBe("Sign in to RecallOps");
    expect(CLERK_LOCALIZATION.signUp.start.title).toBe("Create your RecallOps account");
    expect(JSON.stringify(CLERK_LOCALIZATION)).not.toMatch(/My Application|applicationName|—/);
  });

  it("read the CSS variables the button and links use from the page tokens", () => {
    const css = src("app/globals.css");
    expect(css).toMatch(/:root \{[^}]*--paper: #ffffff;[^}]*--accent: #2672cf;/s);
    expect(css).toMatch(/prefers-color-scheme: dark\)[^}]*--paper: #111110;[^}]*--accent: #3987e5;/s);
  });

  it("frames the development sign-in page the same way, with the settings as code", () => {
    const html = renderToStaticMarkup(<AuthOff />);
    expect(html).toContain("data-auth-brand");
    expect(html).toContain("Sign-in is off</h1>");
    expect(html).toContain('<code class="font-mono text-ink">RECALLOPS_WEB_AUTH=dev</code>');
    expect(html).toContain('href="/app/programs"');
    expect(html).toContain("min-h-dvh content-center");
  });
});

describe("the 404 page", () => {
  it("is branded, centered, and offers a way home", () => {
    const html = renderToStaticMarkup(<NotFound />);
    expect(html).toContain("Page not found</h1>");
    expect(html).toContain("<svg");
    expect(html).toContain('href="/"');
    expect(html).toContain('href="/app/programs"');
    expect(html).toMatch(/<main class="[^"]*min-h-dvh[^"]*content-center justify-items-center[^"]*text-center"/);
    expect(html).not.toMatch(/—/);
  });
});

describe("app icons", () => {
  it("ship the shield mark as an SVG favicon that follows dark mode", () => {
    const svg = src("app/icon.svg");
    expect(svg).toContain('viewBox="0 0 56 56"');
    expect(svg).toContain("M28 13 L41 18 V29 C41 38.5 35.5 44 28 47 C20.5 44 15 38.5 15 29 V18 Z");
    expect(svg).toContain("M22 30 L26.5 34.5 L34.5 25.5");
    expect(svg).toContain("#2672cf");
    expect(svg).toMatch(/prefers-color-scheme: dark\)[\s\S]*#3987e5/);
    expect(svg).not.toContain("currentColor");
  });

  it("ship a 180 by 180 opaque apple-icon.png", () => {
    const png = readFileSync(new URL("../src/app/apple-icon.png", import.meta.url));
    expect(png.subarray(1, 4).toString("latin1")).toBe("PNG");
    expect(png.readUInt32BE(16)).toBe(180); // IHDR width
    expect(png.readUInt32BE(20)).toBe(180); // IHDR height
    expect([2, 0]).toContain(png[25]); // color type: RGB or gray, no alpha (iOS fills transparency with black)
  });
});

describe("the homepage footer", () => {
  it("keeps both notes together on phones, with the source link last", () => {
    const html = renderToStaticMarkup(<Home />);
    const footer = html.slice(html.indexOf("<footer"));
    const project = footer.indexOf("class project");
    const claimants = footer.indexOf("Owners filing a claim");
    const source = footer.indexOf("Source on GitHub");
    expect(project).toBeLessThan(claimants);
    expect(claimants).toBeLessThan(source);
    expect(footer).toContain("md:col-start-2 md:row-span-2 md:row-start-1 md:self-center");
  });
});

describe("the claim form review step", () => {
  it("still renders its first step with the claimant audience wired in", () => {
    const out = renderToStaticMarkup(<ClaimStepper token={T} view={base} />);
    expect(out).toContain("Step 1 of 6");
  });
});
