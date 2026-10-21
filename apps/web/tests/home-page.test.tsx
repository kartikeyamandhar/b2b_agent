// The public homepage at /. It quotes the recall notice the demo is built on, so every quoted run is checked against
// the CPSC record word for word (the same rule the extractor follows: a value that cannot be quoted is not stored).
import { readFileSync } from "node:fs";

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import Home, { metadata } from "@/app/page";
import { NOTICE, REPO_URL, RESULTS, RULES, STEPS } from "@/lib/home-content";

type CpscRecord = { RecallNumber: string; Title: string; URL: string; Description: string; Retailers: { Name: string }[]; Remedies: { Name: string }[] };

const records = JSON.parse(readFileSync(new URL("../../../data/samples/cpsc_sample.json", import.meta.url), "utf8")) as CpscRecord[];
const notice = records.find((r) => r.RecallNumber === NOTICE.number)!;
const squash = (s: string) => s.replace(/\s+/g, " ").trim();
const source = squash([notice.Description, ...notice.Retailers.map((r) => r.Name), ...notice.Remedies.map((r) => r.Name)].join(" "));
const html = renderToStaticMarkup(<Home />);
const text = squash(html.replace(/<[^>]+>/g, " "));

describe("the homepage notice", () => {
  it("quotes CPSC recall 27024 word for word", () => {
    expect(notice.Title).toBe(NOTICE.title);
    expect(notice.URL).toBe(NOTICE.url);
    for (const parts of NOTICE.paragraphs) {
      const quoted = parts.map((p) => (typeof p === "string" ? p : p.text)).join("");
      for (const run of quoted.split("…").map(squash).filter(Boolean)) expect(source, run).toContain(run);
    }
  });

  it("keys every marked phrase to exactly one rule, and every rule to a phrase", () => {
    const marked = NOTICE.paragraphs.flat().flatMap((p) => (typeof p === "string" ? [] : [p.rule]));
    expect([...marked].sort()).toEqual(RULES.map((r) => r.rule));
    for (const r of RULES) expect(html.match(new RegExp(`data-rule="${r.rule}"`, "g"))).toHaveLength(2);
  });
});

describe("the homepage", () => {
  it("offers sign-up, the console, sign-in, and the source", () => {
    for (const href of ["/sign-up", "/app", "/sign-in", REPO_URL]) expect(html).toContain(`href="${href}"`);
    expect(text).toContain("Run a product recall from the CPSC notice to the monthly report.");
  });

  it("shows every step and result, says what is synthetic, and has a title of its own", () => {
    for (const s of STEPS) expect(text).toContain(s.name);
    for (const r of RESULTS) expect(text).toContain(r.result);
    expect(text).toContain("No real person’s data is used.");
    expect(text).toContain("no real money moves");
    expect(metadata.title).toEqual({ absolute: expect.stringContaining("RecallOps") });
  });

  it("uses no em dashes and no three-dot ellipses", () => {
    expect(text).not.toContain("—");
    expect(text).not.toContain("...");
  });

  it("is ordered for assistive technology: one h1, a skip link, and the steps as an ordered list", () => {
    expect(html.match(/<h1/g)).toHaveLength(1);
    expect(html).toContain('href="#main"');
    expect(html).toMatch(/<ol[^>]*>(?:(?!<\/ol>).)*What happens|<ol/);
    expect(html.match(/<li class="relative/g)).toHaveLength(STEPS.length);
  });
});
