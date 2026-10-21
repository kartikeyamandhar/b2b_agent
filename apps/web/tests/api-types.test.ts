import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";
import type { z } from "zod";

import * as api from "@/lib/api-types";

const root = fileURLToPath(new URL("..", import.meta.url));
const contract = JSON.parse(readFileSync(new URL("../../../docs/contracts/openapi.json", import.meta.url), "utf8")) as {
  components: { schemas: Record<string, { examples?: unknown[] }> };
};
const fixture = (name: string): unknown => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), "utf8"));
const schemas = api as unknown as Record<string, z.ZodType | undefined>;

describe("generated api-types.ts", () => {
  it("matches the committed contract (run npm run gen:api when this fails)", () => {
    expect(() => execFileSync(process.execPath, ["scripts/gen-api.mjs", "--check"], { cwd: root, stdio: "pipe" })).not.toThrow();
  });

  // Two documentation examples omit required nullable fields. The API itself always serializes nulls (no
  // exclude_none on any response), so the schemas stay strict and only these exact gaps are tolerated; reported
  // to the backend owner in apps/web/README.md. When the contract fixes them, this test asks to drop the entry.
  const INCOMPLETE_EXAMPLES: Record<string, string[]> = {
    Program: ["closed_at"],
    Trace: ["steps.0.thought"],
  };

  it("parses every example the contract documents", () => {
    const withExamples = Object.entries(contract.components.schemas).filter(([, s]) => s.examples?.length);
    expect(withExamples.length).toBeGreaterThan(0);
    for (const [name, s] of withExamples) {
      const schema = schemas[name];
      expect(schema, name).toBeDefined();
      for (const example of s.examples ?? []) {
        const result = schema!.safeParse(example);
        const missing = result.success ? [] : result.error.issues.map((i) => i.path.join("."));
        expect(missing, `${name} example`).toEqual(INCOMPLETE_EXAMPLES[name] ?? []);
      }
    }
  });

  it("parses a claim detail and a portal view fixture", () => {
    expect(api.ClaimDetail.parse(fixture("claim-detail.json")).payout?.status).toBe("pending_export");
    expect(api.PortalView.parse(fixture("portal-view.json")).program.required_photos).toEqual(["label", "destruction"]);
  });

  it("accepts unknown response enum values and extra fields (CHANGES.md item 5)", () => {
    const claim = { ...(fixture("claim-detail.json") as Record<string, unknown>), status: "archived", new_field: 1 };
    const parsed = api.ClaimDetail.parse(claim);
    expect(parsed.status).toBe("archived");
    expect((parsed as Record<string, unknown>).new_field).toBe(1);
  });

  it("still rejects a response missing a required field", () => {
    const { status: _dropped, ...claim } = fixture("claim-detail.json") as Record<string, unknown>;
    expect(api.ClaimDetail.safeParse(claim).success).toBe(false);
  });

  it("is strict on request-only schemas", () => {
    expect(api.ClaimAction.safeParse({ action: "approve" }).success).toBe(false);
    expect(api.ClaimAction.safeParse({ action: "resume_override", outcome: "approve" }).success).toBe(true);
    const submit = { name: "A", email: "a@example.com", address_line1: "1 St", city: "C", state: "IL", zip: "62701" };
    expect(api.PortalSubmit.safeParse(submit).success).toBe(true);
    expect(api.PortalSubmit.safeParse({ ...submit, quantity: 21 }).success).toBe(false);
    expect(api.PortalPayout.safeParse({ method: "ach_export" }).success).toBe(false);
  });
});
