// QR codes for claim links (owner decision 2026-10-22). A code holds exactly the link, so it opens that one claim with
// the link's own expiry and stops working when the link is reissued; there is no separate public page. Codes are drawn
// from the module matrix as vectors, always dark on white (scanners need the contrast, dark mode included), with the
// standard four-module quiet zone, and at a whole number of pixels per module so no module renders soft or uneven.
import qrcode from "qrcode-generator";

export const QUIET_ZONE = 4;
/** Codes per printed sheet; more links come out as several sheets, so one window never holds thousands of codes. */
export const QR_SHEET_MAX = 1000;

export type QrMatrix = { size: number; dark: (row: number, col: number) => boolean };
export type QrItem = { label: string; url: string };

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

/** A standalone SVG document of the code, for download and the printed sheet. */
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

/** The 1-based ranges of the sheets `count` links print as, QR_SHEET_MAX codes per sheet. */
export function qrSheetRanges(count: number, max = QR_SHEET_MAX): [first: number, last: number][] {
  const ranges: [number, number][] = [];
  for (let first = 1; first <= count; first += max) ranges.push([first, Math.min(count, first + max - 1)]);
  return ranges;
}

/** A self-contained, printable page of codes, three to a row on Letter or A4, each with its claim and its link spelled
 *  out for anyone who cannot scan. No scripts and nothing loaded from elsewhere. */
export function qrSheetHtml(items: readonly QrItem[], heading: string): string {
  const cards = items
    .map(
      (i) =>
        `<figure class="card">${qrSvg(i.url, 320)}<figcaption><span class="id">${escapeHtml(i.label)}</span>` +
        `<span class="how">Scan to file your recall claim</span><span class="url">${escapeHtml(i.url)}</span></figcaption></figure>`,
    )
    .join("");
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>${escapeHtml(heading)}</title><style>
@page{size:auto;margin:0.5in}
*{box-sizing:border-box}
body{margin:0;padding:24px;background:#fff;color:#1f1d1a;font:12px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{max-width:7.5in;margin:0 auto 16px}
h1{margin:0 0 4px;font-size:16px;font-weight:600}
header p{margin:0;color:#5c5852}
.sheet{display:grid;grid-template-columns:repeat(3,1fr);gap:0.2in;max-width:7.5in;margin:0 auto}
.card{margin:0;padding:0.14in;border:1px solid #d9d5ce;border-radius:6px;break-inside:avoid;text-align:center}
.card svg{display:block;width:1.6in;height:1.6in;margin:0 auto}
figcaption{display:grid;gap:2px;margin-top:6px}
.id{font-weight:600}
.how{color:#5c5852}
.url{font:7px/1.3 ui-monospace,SFMono-Regular,Menlo,monospace;color:#5c5852;overflow-wrap:anywhere}
@media print{body{padding:0}header p{display:none}}
</style></head><body><header><h1>${escapeHtml(heading)}</h1><p>Print this page or save it as a PDF. Each code opens one claim, like its link, and works like a password: send each one only to its claimant. A code stops working when its link expires or is reissued.</p></header><main class="sheet">${cards}</main></body></html>`;
}
