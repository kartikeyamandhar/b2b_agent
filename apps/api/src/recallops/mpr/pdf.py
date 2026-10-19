"""A one-page text PDF for the MPR, written by hand (PDF 1.4, Helvetica, no dependency). Enough for a firm to file
or archive the numbers; the CPSC submission itself is a web form."""

from __future__ import annotations

from typing import Any


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _ascii(text: str) -> str:
    return text.encode("ascii", "replace").decode("ascii")


def render_pdf(title: str, period: str, boxes: dict[str, Any], notes: list[str]) -> bytes:
    lines = [f"Monthly Progress Report: {title}", f"Period: {period}", ""]
    lines += [f"{k.replace('_', ' ')}: {v}" for k, v in boxes.items()]
    lines += ["", "Notes:"] + [f"- {n}" for n in notes]
    y, ops = 760, ["BT", "/F1 10 Tf"]
    for i, line in enumerate(lines[:60]):
        size = 14 if i == 0 else 10
        ops.append(f"/F1 {size} Tf 1 0 0 1 50 {y} Tm ({_escape(_ascii(line)[:110])}) Tj")
        y -= 18 if i == 0 else 12
    ops.append("ET")
    stream = "\n".join(ops).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
