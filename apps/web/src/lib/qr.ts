// QR codes for claim links (owner decision 2026-10-22). A code holds exactly the link: a product's public claim link
// (plan v3: anyone can start a claim from it, and it stops when the link is replaced) or one claim's private link
// (it stops when that link is reissued). Codes are drawn from the module matrix as vectors, always dark on white
// (scanners need the contrast, dark mode included), with the standard four-module quiet zone, and at a whole number of
// pixels per module so no module renders soft or uneven.
import qrcode from "qrcode-generator";

export const QUIET_ZONE = 4;

export type QrMatrix = { size: number; dark: (row: number, col: number) => boolean };

/** The module matrix for a link. Links are printable ASCII (an https URL with a base64url token); anything else is
 *  refused rather than encoded in a way a phone could read differently. Error correction M survives normal print wear. */
export function qrMatrix(text: string): QrMatrix {
  if (!/^[\x20-\x7e]{1,2000}$/.test(text)) throw new Error("a QR code holds one printable ASCII link");
  const qr = qrcode(0, "M");
  qr.addData(text, "Byte");
  qr.make();
  return { size: qr.getModuleCount(), dark: (row, col) => qr.isDark(row, col) };
}

/** Width of the drawn code in modules, quiet zone included. */
export function qrSpan(m: QrMatrix): number {
  return m.size + 2 * QUIET_ZONE;
}

/** The dark modules as one SVG path, a rectangle per run of dark modules in a row, offset by the quiet zone. */
export function qrPath(m: QrMatrix): string {
  const parts: string[] = [];
  for (let row = 0; row < m.size; row++) {
    let col = 0;
    while (col < m.size) {
      if (!m.dark(row, col)) {
        col++;
        continue;
      }
      let end = col;
      while (end < m.size && m.dark(row, end)) end++;
      parts.push(`M${col + QUIET_ZONE} ${row + QUIET_ZONE}h${end - col}v1h-${end - col}z`);
      col = end;
    }
  }
  return parts.join("");
}

/** The drawn size nearest `target` pixels that is a whole number of pixels per module (at least 2). */
export function qrPixels(m: QrMatrix, target: number): number {
  const span = qrSpan(m);
  return span * Math.max(2, Math.round(target / span));
}

/** A standalone SVG document of the code, for download and the printed poster. */
export function qrSvg(text: string, target = 512): string {
  const m = qrMatrix(text);
  const span = qrSpan(m);
  const px = qrPixels(m, target);
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${span} ${span}" width="${px}" height="${px}" ` +
    `shape-rendering="crispEdges"><rect width="${span}" height="${span}" fill="#fff"/>` +
    `<path d="${qrPath(m)}" fill="#000"/></svg>`
  );
}

/** Pixel rectangles for drawing the code on a canvas (the PNG download), `scale` pixels per module. */
export function qrRects(m: QrMatrix, scale: number): [x: number, y: number, w: number, h: number][] {
  const rects: [number, number, number, number][] = [];
  for (const run of qrPath(m).matchAll(/M(\d+) (\d+)h(\d+)/g)) {
    rects.push([Number(run[1]) * scale, Number(run[2]) * scale, Number(run[3]) * scale, scale]);
  }
  return rects;
}

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const escapeHtml = (s: string) => s.replace(/[&<>"']/g, (c) => ESCAPES[c]!);

export type QrPosterFacts = { company: string | null; title: string; url: string; models: readonly string[]; moreModels: number };

/** The two lines under the code: how to open it, and what the claim asks for first. */
export const POSTER_INSTRUCTIONS = [
  "Scan the code with your phone's camera, or type the address into a browser.",
  "Have the product with you: the claim asks for a photo of its label. No account is needed.",
] as const;

/** One printable page for a product's public claim link: the company and product, "Scan to file a claim", the code,
 *  the link spelled out, the models, and two lines of instruction. The note above the poster is for the screen only;
 *  print styles hide it, so the page prints as the poster alone. No scripts and nothing loaded from elsewhere. */
export function qrPosterHtml(p: QrPosterFacts): string {
  const models = p.models.length
    ? `<p class="models"><span class="label">${p.models.length + p.moreModels === 1 ? "Model" : "Models"}</span> ${p.models.map(escapeHtml).join(", ")}${
        p.moreModels > 0 ? ` and ${p.moreModels} more` : ""
      }</p>`
    : "";
  const company = p.company ? `<p class="company">${escapeHtml(p.company)}</p>` : "";
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Claim poster: ${escapeHtml(p.title)}</title><style>
@page{size:auto;margin:0.6in}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:#fff;color:#1f1d1a;font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.screen-note{max-width:6.5in;margin:0 auto 24px;padding:8px 12px;border:1px solid #d9d5ce;border-radius:6px;font-size:13px;color:#5c5852}
.poster{max-width:6.5in;margin:0 auto;text-align:center;break-inside:avoid;page-break-inside:avoid}
.company{margin:0;font-size:18px;color:#5c5852}
h1{margin:4px 0 20px;font-size:26px;line-height:1.25;font-weight:600}
.scan{margin:0 0 14px;font-size:30px;font-weight:700}
.poster svg{display:block;width:3.4in;height:3.4in;margin:0 auto}
.url{margin:14px auto 0;max-width:6in;font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;color:#3a3732;overflow-wrap:anywhere}
.models{margin:18px 0 0;font-size:15px}
.label{font-weight:600}
.how{margin:18px auto 0;max-width:5.5in;font-size:15px;color:#3a3732}
.how span{display:block}
@media print{body{padding:0}.screen-note{display:none}}
</style></head><body><p class="screen-note">Print this page or save it as a PDF. It fits one sheet of Letter or A4. The code and the link stop working when the link is replaced.</p><main class="poster">${company}<h1>${escapeHtml(p.title)}</h1><p class="scan">Scan to file a claim</p>${qrSvg(p.url, 480)}<p class="url">${escapeHtml(p.url)}</p>${models}<p class="how">${POSTER_INSTRUCTIONS.map((l) => `<span>${escapeHtml(l)}</span>`).join("")}</p></main></body></html>`;
}
