"""Program documents: the recall notice and a plain-language claims FAQ written from the program's rules.

Real programs would also carry the firm's corrective action plan and warranty terms; the project has none, so the
FAQ restates the program's claim policy in prose (the same policy the agent's instructions state), plus the facts a
claimant or reviewer asks about. It never mentions how synthetic claims are built.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from recallops.claims.tools.checks import destruction_required, receipt_required
from recallops.rules.schemas import ExtractedRules

MAX_CHUNK_CHARS = 700


@dataclass(frozen=True)
class ProgramDocument:
    kind: str  # notice | faq | cap | warranty | other
    title: str
    text: str


def _money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


def claims_faq(title: str, rules: ExtractedRules, value_threshold_cents: int) -> str:
    ident, remedy = rules.identification, rules.remedy
    parts: list[str] = [f"Claims FAQ for: {title}"]
    if ident.models:
        where = f" The model number is {ident.model_label_location}." if ident.model_label_location else ""
        parts.append(
            "Which units are recalled? Units with these model numbers: "
            + ", ".join(ident.models)
            + "."
            + where
            + " The model is confirmed from the photo of the product label; a clear label photo showing a recalled"
            " model confirms the model even if the typed model number has a typo or is missing. A label showing a"
            " model that is not on the list means the unit is not part of this recall."
        )
    if ident.serial_ranges:
        parts.append(
            "Do serial numbers matter? Yes. Only these serial numbers are included: "
            + "; ".join(r.raw for r in ident.serial_ranges)
            + ". A serial number outside these ranges is not part of the recall."
        )
    if ident.date_codes:
        parts.append(
            "Do date codes matter? Yes. Only these date codes are included: "
            + "; ".join(d.raw for d in ident.date_codes)
            + ". A date code outside them is not part of the recall."
        )
    sold = []
    if ident.sold_at:
        sold.append("The product was sold at " + ", ".join(ident.sold_at) + ".")
    if ident.sold_from or ident.sold_to:
        sold.append(
            f"It was sold from {ident.sold_from or 'an unstated date'} to {ident.sold_to or 'an unstated date'}."
        )
    if sold:
        parts.append(
            "Where and when was it sold? "
            + " ".join(sold)
            + " A purchase dated before the sale window is not eligible. Owners who bought secondhand, received the"
            " product as a gift, or bought from a store not on the list are still eligible: they are asked for proof"
            " of ownership instead of being denied. A purchase date after the window or an unknown date is asked"
            " about, not denied."
        )
    proof = ["a photo of the product label"]
    if receipt_required(remedy):
        proof.append("a receipt or other proof of purchase")
    if destruction_required(remedy):
        instruction = remedy.destruction_instruction or "destroy the product as instructed"
        proof.append(f"a photo showing the product destroyed as instructed ({instruction})")
    parts.append(
        "What proof is needed? "
        + "; ".join(proof)
        + ". A destruction photo that is missing, that repeats the label photo, or that does not show the instructed"
        " destruction is asked for again."
    )
    if remedy.remedy_type:
        option_text = ", ".join(sorted({o.type for o in remedy.options})) or remedy.remedy_type
        parts.append(f"What remedy is offered? {option_text}.")
    parts.append(
        "How are duplicate or suspicious claims handled? One remedy per recalled unit. Claims that share a photo, an"
        " address or email, a serial number, or a payout account with another claim are sent to a person for review,"
        " together with the claims they match."
    )
    parts.append(
        f"Who reviews a claim? Claims are checked automatically. Refunds over {_money(value_threshold_cents)}, claims"
        " flagged as possible duplicates, and claims the checker could not finish are reviewed by a person. If the"
        " label photo is too blurry to read, the claimant is asked for a clearer photo."
    )
    if remedy.contact:
        parts.append(f"How do I contact the firm? {remedy.contact}.")
    return "\n\n".join(parts)


def program_documents(
    title: str, source_text: str, rules: ExtractedRules, value_threshold_cents: int
) -> list[ProgramDocument]:
    return [
        ProgramDocument("notice", title, source_text),
        ProgramDocument("faq", f"Claims FAQ: {title}", claims_faq(title, rules, value_threshold_cents)),
    ]


def chunk(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Paragraphs (or notice sections), merged up to max_chars; an oversized paragraph is split on sentences."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    pieces: list[str] = []
    for p in paragraphs:
        if len(p) <= max_chars:
            pieces.append(p)
            continue
        sentence_buf = ""
        for sentence in re.split(r"(?<=[.!?])\s+", p):
            if sentence_buf and len(sentence_buf) + len(sentence) + 1 > max_chars:
                pieces.append(sentence_buf)
                sentence_buf = sentence
            else:
                sentence_buf = f"{sentence_buf} {sentence}".strip()
        if sentence_buf:
            pieces.append(sentence_buf)
    chunks: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + len(piece) + 2 > max_chars:
            chunks.append(buf)
            buf = piece
        else:
            buf = f"{buf}\n\n{piece}".strip()
    if buf:
        chunks.append(buf)
    return chunks
