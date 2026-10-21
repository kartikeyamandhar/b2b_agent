import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { POST } from "@/app/api/[...path]/route";
import ClaimFormPage from "@/app/claim/[token]/page";
import ClaimStatusPage from "@/app/claim/[token]/status/page";

vi.mock("next/navigation", async (orig) => ({ ...(await orig<typeof import("next/navigation")>()), useRouter: () => ({ push: vi.fn(), refresh: vi.fn(), replace: vi.fn() }) }));

const view = JSON.parse(readFileSync(new URL("./fixtures/portal-view.json", import.meta.url), "utf8")) as Record<string, unknown>;
const T = "dev.00000000-0000-4000-8000-00000000d001.66dcc492-086f-5b59-b441-cbe001eb2e0b";
let calls: Array<{ url: string; headers: Headers; body: unknown }>;

function serve(respond: () => Response) {
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    calls.push({ url, headers: new Headers(init.headers), body: init.body });
    return respond();
  });
}
const params = (token: string) => ({ params: Promise.resolve({ token }) });

beforeEach(() => {
  calls = [];
  vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
  vi.stubEnv("RECALLOPS_WEB_AUTH", "dev");
  vi.stubEnv("RECALLOPS_DEV_FIRM_ID", "00000000-0000-4000-8000-00000000d001");
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("/claim/[token]", () => {
  it("reads the claim with the token only and starts the form", async () => {
    serve(() => Response.json(view));
    const out = renderToStaticMarkup(await ClaimFormPage(params(T)));
    expect(calls[0]!.url).toBe(`http://127.0.0.1:8000/portal/${T}`);
    expect(calls[0]!.headers.get("x-dev-firm-id")).toBeNull();
    expect(out).toContain("Example Thermal Laminators");
    expect(out).toContain("Step 1 of 6");
  });

  it("sends a submitted claim to its status page", async () => {
    serve(() => Response.json({ ...view, status: "submitted" }));
    const err = await ClaimFormPage(params(T)).catch((e: unknown) => e);
    expect(String((err as { digest?: string }).digest)).toContain(`/claim/${T}/status`);
  });

  it("answers an implausible token locally", async () => {
    serve(() => Response.json(view));
    const out = renderToStaticMarkup(await ClaimFormPage(params("../../v1/programs")));
    expect(calls).toHaveLength(0);
    expect(out).toContain("This claim link is not valid or has expired");
  });

  it.each([
    [404, "not valid or has expired"],
    [429, "Wait a few minutes"],
    [503, "Nothing is lost"],
  ])("explains a %i in plain words", async (status, text) => {
    serve(() => Response.json({ title: "x", status, detail: "d", correlation_id: `c-${status}` }, { status }));
    const out = renderToStaticMarkup(await ClaimFormPage(params(T)));
    expect(out).toContain(text);
    expect(out).toContain(`c-${status}`);
  });
});

describe("/claim/[token]/status", () => {
  it("renders the status server-side", async () => {
    serve(() => Response.json({ ...view, status: "approved", payout_status: "pending_export" }));
    const out = renderToStaticMarkup(await ClaimStatusPage(params(T)));
    expect(out).toContain("Your claim was approved");
    expect(out).toContain("A check will be mailed to you");
    expect(out).toContain("Update mailing address");
  });

  it("points a draft back to the form", async () => {
    serve(() => Response.json(view));
    const out = renderToStaticMarkup(await ClaimStatusPage(params(T)));
    expect(out).toContain(`href="/claim/${T}"`);
  });
});

describe("portal photo upload through the proxy", () => {
  it("streams the multipart body to the API with no identity", async () => {
    vi.stubEnv("RECALLOPS_API_URL", "http://127.0.0.1:8000");
    let received: { url: string; headers: Headers; text: string } | null = null;
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      received = { url, headers: new Headers(init.headers), text: await new Response(init.body as BodyInit).text() };
      return Response.json({ id: "p1", kind: "label", sha256: "0".repeat(64), phash: "f".repeat(16), uploaded_at: "2026-10-20T08:00:00Z", url: "/v1/claims/c/photos/p1" });
    });
    const form = new FormData();
    form.set("kind", "label");
    form.set("file", new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xd9])], { type: "image/jpeg" }), "label.jpg");
    const req = new Request(`http://localhost:3000/api/portal/${T}/photos`, { method: "POST", body: form, headers: { origin: "http://localhost:3000", host: "localhost:3000" } });
    const res = await POST(req, { params: Promise.resolve({ path: ["portal", T, "photos"] }) });
    expect(res.status).toBe(200);
    expect(received!.url).toBe(`http://127.0.0.1:8000/portal/${T}/photos`);
    expect(received!.headers.get("content-type")).toMatch(/^multipart\/form-data; boundary=/);
    expect(received!.headers.get("x-dev-firm-id")).toBeNull();
    expect(received!.text).toContain('name="kind"');
    expect(received!.text).toContain('filename="label.jpg"');
  });
});
