"""The claim policy (evals/adjudication/outcome_policy.yaml, photo_rules included) in two forms that must agree:
POLICY_TEXT for the agent's instructions, and decide_table for the rules_plus_vision row. Every row that reads
photos gets the policy through one of these, never only through retrieved documents (PREREG Revision 2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from recallops.claims.tools import DuplicatesCheck, IdentityCheck, PurchaseCheck
from recallops.claims.vision import DestructionReading, LabelReading

Outcome = Literal["approve", "deny", "needs_info", "flag"]
PRECEDENCE: tuple[Outcome, ...] = ("flag", "deny", "needs_info", "approve")
DECISION_TABLE_VERSION = "2.0.0"

POLICY_TEXT = """Claim policy (apply exactly; when several apply, the outcome is the first of flag, deny, needs_info,
approve that any rule triggers):
- flag: check_duplicates finds any other claim sharing a photo, an address or email, a serial number, or a payout
  account.
- Model: the label photo is the evidence. If the label is legible and shows a recalled model, the model is confirmed,
  even when the typed model has a typo or is missing. If the legible label shows a model that is not recalled: deny.
  If the label photo is unreadable (read_label_photo returns legible false or no model text): needs_info, asking for
  a clearer label photo. Without a usable label reading, the typed model decides: not recalled is deny; missing or
  ambiguous is needs_info.
- Serial number or date code (only when the program has those rules, using the typed values): outside the recalled
  ranges is deny; missing is needs_info.
- Purchase: bought before the sale window is deny; after the window or an unknown date is needs_info; a retailer not
  on the list or unknown is needs_info (proof of ownership is asked for; secondhand and gift owners are eligible); a
  required receipt that is missing is needs_info.
- Destruction (only when the remedy requires it): a missing destruction photo, one that repeats the label photo, or
  one that does not show the instructed destruction is needs_info.
- Otherwise approve. A refund over the value threshold is still approved; a person reviews it afterwards.
Every reason must cite rule ids (R:...) or tool result ids (T1, T2, ...). For needs_info, write
question_for_claimant to the claimant in plain language."""


@dataclass(frozen=True)
class TableInputs:
    typed_model: IdentityCheck
    serial: IdentityCheck
    date_code: IdentityCheck
    purchase: PurchaseCheck
    duplicates: DuplicatesCheck
    destruction_required: bool
    destruction_presence: str  # present | missing | not_required (pure-code presence check)
    destruction_reason: str
    label: LabelReading | None  # None when there is no label photo or the reading failed
    label_model: IdentityCheck | None  # check_model on the label's model text, when legible
    destruction: DestructionReading | None


@dataclass(frozen=True)
class TableResult:
    outcome: Outcome
    reasons: list[str]


def decide_table(t: TableInputs) -> TableResult:
    triggered: dict[Outcome, list[str]] = {o: [] for o in PRECEDENCE}
    for hit in t.duplicates.hits:
        triggered["flag"].append(f"duplicate {hit.kind} with claim {hit.other_claim_id} ({hit.detail})")
    label_usable = t.label is not None and t.label.legible and bool(t.label.model_text) and t.label_model is not None
    if label_usable:
        assert t.label_model is not None
        if t.label_model.verdict == "mismatch":
            triggered["deny"].append("the label shows a model that is not recalled")
        elif t.label_model.verdict == "ambiguous":
            triggered["needs_info"].append("the label's model number could not be confirmed")
    elif t.label is not None:
        triggered["needs_info"].append("the label photo is unreadable; a clearer photo is needed")
    elif t.typed_model.verdict == "mismatch":
        triggered["deny"].append("claimed model is not a recalled model")
    elif t.typed_model.verdict in ("not_provided", "ambiguous"):
        triggered["needs_info"].append(f"model {t.typed_model.verdict}")
    if t.serial.verdict == "out_of_range":
        triggered["deny"].append("serial outside the recalled ranges")
    elif t.serial.verdict == "not_provided":
        triggered["needs_info"].append("serial number needed")
    if t.date_code.verdict == "out_of_range":
        triggered["deny"].append("date code outside the recalled range")
    elif t.date_code.verdict in ("not_provided", "unparsed"):
        triggered["needs_info"].append(f"date code {t.date_code.verdict}")
    if t.purchase.window == "before":
        triggered["deny"].append("purchased before the recalled units were sold")
    elif t.purchase.window in ("after", "unknown"):
        triggered["needs_info"].append(f"purchase date {t.purchase.window}")
    if t.purchase.retailer in ("not_listed", "unknown"):
        triggered["needs_info"].append(f"retailer {t.purchase.retailer}; proof of purchase needed")
    if t.purchase.receipt == "missing":
        triggered["needs_info"].append("receipt required")
    if t.destruction_required:
        if t.destruction_presence == "missing":
            triggered["needs_info"].append(f"destruction photo needed: {t.destruction_reason}")
        elif t.destruction is None or not t.destruction.complies:
            triggered["needs_info"].append("the destruction photo does not show the instructed destruction")
    outcome: Outcome = next(o for o in PRECEDENCE if triggered[o] or o == "approve")
    return TableResult(outcome=outcome, reasons=triggered[outcome] or ["every check passed"])
