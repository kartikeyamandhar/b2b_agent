import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ProblemAlert } from "@/components/ProblemAlert";
import { StatusBadge } from "@/components/StatusBadge";

describe("StatusBadge", () => {
  it("renders the label and tone", () => {
    const html = renderToStaticMarkup(<StatusBadge kind="claim" value="flagged" />);
    expect(html).toContain("Flagged");
    expect(html).toContain('data-tone="attention"');
  });

  it("marks an unknown value with a title", () => {
    const html = renderToStaticMarkup(<StatusBadge kind="claim" value="archived" />);
    expect(html).toContain("In progress");
    expect(html).toContain("Unrecognized status");
  });
});

describe("ProblemAlert", () => {
  it("announces the sentence, detail, and correlation id", () => {
    const html = renderToStaticMarkup(
      <ProblemAlert problem={{ status: 409, title: "Conflict", detail: "a review is already pending", correlationId: "c-42", retryAfterSeconds: null }} />,
    );
    expect(html).toContain('role="alert"');
    expect(html).toContain("Refresh the page");
    expect(html).toContain("a review is already pending");
    expect(html).toContain("c-42");
  });

  it("escapes API detail text", () => {
    const html = renderToStaticMarkup(
      <ProblemAlert problem={{ status: 422, title: "x", detail: "<script>alert(1)</script>", correlationId: null, retryAfterSeconds: 5 }} />,
    );
    expect(html).not.toContain("<script>");
    expect(html).toContain("try again in 5 seconds");
  });
});
