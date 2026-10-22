// The reply to a "we need more information" question, after the first live claim looped (2026-10-21): the claimant
// corrects the details the question is about or adds the photo it asks for, and a note alone goes to a person.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: () => {} }) }));

import { DecisionPanel, MessagesPanel } from "@/components/ClaimPanels";
import { NeedsInfoReply } from "@/components/portal/NeedsInfoReply";
import type { PortalAnswerField, PortalView } from "@/lib/api-types";
import { ANSWER_OUTCOME_TEXT, answerBody, answerOutcome, answerPhotoKinds } from "@/lib/portal";

const FIELDS: PortalAnswerField[] = [
  { name: "retailer_claimed", value: "Amazon" },
  { name: "purchase_date", value: null },
];
const program = {
  title: "Primark Recalls 101 Dalmatians Coin Purses",
  models: ["2595301"],
  model_label_location: "on the product tag attached to the zip puller",
  remedy_type: "refund",
  destruction_instruction: null,
  required_photos: ["label", "receipt"],
} as PortalView["program"];

describe("the reply body", () => {
  it("sends the note and only the details the claimant changed", () => {
    expect(answerBody("  hi  ", {}, FIELDS)).toEqual({ message: "hi" });
    expect(answerBody("", { retailer_claimed: "Amazon" }, FIELDS)).toEqual({ message: "" }); // unchanged
    expect(answerBody("", { retailer_claimed: " Primark ", purchase_date: "2026-04-02" }, FIELDS)).toEqual({
      message: "",
      retailer_claimed: "Primark",
      purchase_date: "2026-04-02",
    });
  });

  it("says before sending what will happen", () => {
    expect(answerOutcome({ message: "", retailer_claimed: "Primark" }, false)).toBe("rechecked");
    expect(answerOutcome({ message: "" }, true)).toBe("rechecked");
    expect(answerOutcome({ message: "I lost the receipt" }, false)).toBe("to_person");
    expect(answerOutcome({ message: "" }, false)).toBe("empty");
    expect(ANSWER_OUTCOME_TEXT.to_person).toContain("A person at the company will read your note");
  });

  it("offers the photos the question asks for, else the program's required photos, plus an extra", () => {
    expect(answerPhotoKinds({ answer_photos: ["label"], program })).toEqual(["label", "other"]);
    expect(answerPhotoKinds({ answer_photos: [], program })).toEqual(["label", "receipt", "other"]);
  });
});

describe("the reply form", () => {
  it("shows the details asked about, prefilled, with a date picker for the date", () => {
    const html = renderToStaticMarkup(<NeedsInfoReply token="t" view={{ program, answer_fields: FIELDS, answer_photos: ["label"] }} />);
    expect(html).toContain("Where you bought it");
    expect(html).toMatch(/data-answer-field="retailer_claimed"[^>]*|value="Amazon"/);
    expect(html).toContain('type="date"');
    expect(html.match(/data-answer-photo="/g)).toHaveLength(2); // label and an extra
    expect(html).toContain('data-answer-outcome="empty"');
  });
});

describe("the console thread", () => {
  it("lists questions and replies, and says why a held reply waits", () => {
    const html = renderToStaticMarkup(
      <MessagesPanel
        messages={[
          { sender: "firm", text: "Where did you buy it?", at: "2026-10-21T23:49:31Z" },
          { sender: "claimant", text: "Amazon, 25th October 2025", at: "2026-10-21T23:50:02Z" },
        ]}
      />,
    );
    expect(html).toContain("Question from the firm");
    expect(html).toContain("Amazon, 25th October 2025");
    expect(renderToStaticMarkup(<MessagesPanel messages={[]} />)).toBe("");
    const held = renderToStaticMarkup(
      <DecisionPanel
        claimId="c"
        decidedAt={null}
        decision={{ outcome: "needs_info", reasons: [], cited_rules: [], routed_to_human: true, hold_reason: "claimant_reply" } as never}
      />,
    );
    expect(held).toContain("The claimant replied with a note but changed no detail or photo.");
  });
});
