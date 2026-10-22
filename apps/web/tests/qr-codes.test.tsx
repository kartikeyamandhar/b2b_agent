// QR codes for claim links (owner decision 2026-10-22): each code holds exactly its claim's link, drawn sharp and
// dark on white, with a printable sheet for links issued in bulk.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: () => {}, push: () => {} }), usePathname: () => "/app/programs" }));

import { IssuedLinkList } from "@/components/IssueLinks";
import { QrCode, QrPanel, openQrSheet } from "@/components/QrCode";
import { ReissuedLink } from "@/components/ReissueLink";
import { QR_SHEET_MAX, QUIET_ZONE, qrMatrix, qrPath, qrPixels, qrRects, qrSheetHtml, qrSheetRanges, qrSpan, qrSvg } from "@/lib/qr";

const TOKEN = "A".repeat(48) + "b-_9".repeat(12); // 96 characters, like a signed claim token
const LINK = `https://recallops-five.vercel.app/claim/${TOKEN}`;
const CLAIM = "3f2a9c1e-0000-4000-8000-000000000001";

/** Rebuild the matrix from the drawn path, to prove the drawing is exactly the library's modules. */
function fromPath(d: string, size: number): boolean[][] {
  const grid = Array.from({ length: size }, () => Array<boolean>(size).fill(false));
  for (const [, x, y, w] of d.matchAll(/M(\d+) (\d+)h(\d+)v1h-\d+z/g)) {
    for (let c = Number(x); c < Number(x) + Number(w); c++) grid[Number(y) - QUIET_ZONE]![c - QUIET_ZONE] = true;
  }
  return grid;
}

describe("the QR matrix", () => {
  it("holds a claim link at error correction M in a standard size, with its three finder patterns", () => {
    const m = qrMatrix(LINK);
    expect((m.size - 17) % 4).toBe(0);
    expect(m.size).toBeLessThanOrEqual(61); // version 11 at most for a 136-character link
    const finder = (r0: number, c0: number) => {
      for (let r = 0; r < 7; r++)
        for (let c = 0; c < 7; c++) {
          const ring = Math.max(Math.abs(r - 3), Math.abs(c - 3));
          expect(m.dark(r0 + r, c0 + c)).toBe(ring !== 2);
        }
    };
    finder(0, 0);
    finder(0, m.size - 7);
    finder(m.size - 7, 0);
  });

  it("is drawn module for module, deterministically, and differs for another link", () => {
    const m = qrMatrix(LINK);
    const grid = fromPath(qrPath(m), m.size);
    for (let r = 0; r < m.size; r++) for (let c = 0; c < m.size; c++) expect(grid[r]![c]).toBe(m.dark(r, c));
    expect(qrPath(qrMatrix(LINK))).toBe(qrPath(m));
    expect(qrPath(qrMatrix(`${LINK}x`))).not.toBe(qrPath(m));
  });

  it("refuses anything but one printable ASCII link", () => {
    expect(() => qrMatrix("")).toThrow();
    expect(() => qrMatrix("https://example.com/claim/é")).toThrow();
    expect(() => qrMatrix("https://example.com/\nclaim")).toThrow();
    expect(() => qrMatrix("x".repeat(2001))).toThrow();
  });

  it("is drawn at a whole number of pixels per module, at least two", () => {
    const m = qrMatrix(LINK);
    for (const target of [40, 168, 171, 512, 1024]) {
      const px = qrPixels(m, target);
      expect(px % qrSpan(m)).toBe(0);
      expect(px / qrSpan(m)).toBeGreaterThanOrEqual(2);
      expect(Math.abs(px - target)).toBeLessThanOrEqual(target < 2 * qrSpan(m) ? 2 * qrSpan(m) : qrSpan(m) / 2);
    }
  });

  it("makes a standalone SVG, white quiet zone included, and PNG rectangles at the chosen scale", () => {
    const svg = qrSvg(LINK, 512);
    const span = qrSpan(qrMatrix(LINK));
    expect(svg).toContain(`viewBox="0 0 ${span} ${span}"`);
    expect(svg).toContain('shape-rendering="crispEdges"');
    expect(svg).toContain(`<rect width="${span}" height="${span}" fill="#fff"/>`);
    const rects = qrRects(qrMatrix(LINK), 16);
    expect(rects.length).toBe(qrPath(qrMatrix(LINK)).split("M").length - 1);
    expect(rects.every(([x, y, w, h]) => x % 16 === 0 && y % 16 === 0 && w % 16 === 0 && h === 16 && x >= QUIET_ZONE * 16)).toBe(true);
  });
});

describe("the printed sheet", () => {
  it("splits big batches into sheets of QR_SHEET_MAX", () => {
    expect(qrSheetRanges(0)).toEqual([]);
    expect(qrSheetRanges(1)).toEqual([[1, 1]]);
    expect(qrSheetRanges(QR_SHEET_MAX)).toEqual([[1, QR_SHEET_MAX]]);
    expect(qrSheetRanges(2500)).toEqual([
      [1, 1000],
      [1001, 2000],
      [2001, 2500],
    ]);
  });

  it("is one page with a code, its claim, and its link per item, escaped and without scripts", () => {
    const html = qrSheetHtml(
      [
        { label: "Claim 3f2a9c1e", url: LINK },
        { label: "<script>alert(1)</script>", url: `${LINK}&x="y"` },
      ],
      "Claim QR codes: Acme <Kettle>",
    );
    expect(html.match(/<svg /g)).toHaveLength(2);
    expect(html).toContain("Claim 3f2a9c1e");
    expect(html).toContain(LINK);
    expect(html).not.toContain("<script");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&amp;x=&quot;y&quot;");
    expect(html).toContain("<title>Claim QR codes: Acme &lt;Kettle&gt;</title>");
    expect(html).toContain("break-inside:avoid");
  });

  it("opens in a new window with no handle back to the console, and says so when pop-ups are blocked", () => {
    const written: string[] = [];
    const fake = {
      opener: {} as unknown,
      document: { open: vi.fn(), write: (h: string) => written.push(h), close: vi.fn() },
      focus: vi.fn(),
      print: vi.fn(),
    };
    expect(openQrSheet([{ label: "Claim 1", url: LINK }], "Sheet", () => fake as unknown as Window)).toBe(true);
    expect(fake.opener).toBeNull();
    expect(fake.print).toHaveBeenCalledOnce();
    expect(written.join("")).toContain("<svg ");
    expect(openQrSheet([{ label: "Claim 1", url: LINK }], "Sheet", () => null)).toBe(false);
  });
});

describe("QR codes in the console", () => {
  it("draws the code as a labelled image, dark on white", () => {
    const html = renderToStaticMarkup(<QrCode value={LINK} label="QR code for claim 3f2a9c1e" />);
    const m = qrMatrix(LINK);
    expect(html).toContain('role="img"');
    expect(html).toContain('aria-label="QR code for claim 3f2a9c1e"');
    expect(html).toContain(`width="${qrPixels(m, 168)}"`);
    expect(html).toContain('fill="#fff"');
    expect(html).toContain('fill="#000"');
    expect(renderToStaticMarkup(<QrCode value={"no\nlink"} label="x" />)).toContain("cannot be drawn");
  });

  it("offers SVG and PNG downloads beside each code", () => {
    const html = renderToStaticMarkup(<QrPanel url={LINK} claimId={CLAIM} />);
    expect(html).toContain("Download SVG");
    expect(html).toContain("Download PNG");
    expect(html).toContain("QR code for the link of claim 3f2a9c1e");
  });

  it("puts a QR button on every issued link and a print button per sheet", () => {
    const links = Array.from({ length: 1500 }, (_, i) => ({ claim_id: `${String(i).padStart(8, "0")}-0000-4000-8000-000000000000`, url: `${LINK}${i}` }));
    const html = renderToStaticMarkup(<IssuedLinkList links={links} programTitle="Acme Kettle" programId={CLAIM} />);
    expect(html.match(/data-show-qr=/g)).toHaveLength(1500);
    expect(html).toContain("Print QR codes 1 to 1,000");
    expect(html).toContain("Print QR codes 1,001 to 1,500");
    expect(html).toContain('aria-expanded="false"');
    const one = renderToStaticMarkup(<IssuedLinkList links={links.slice(0, 3)} programTitle="" programId={CLAIM} />);
    expect(one.match(/data-print-qr/g)).toHaveLength(1);
    expect(one).toContain(">Print QR codes<");
    expect(one).toContain("and its QR code, opens one claim");
  });

  it("shows the reissued link's code with it", () => {
    const html = renderToStaticMarkup(<ReissuedLink link={LINK} claimId={CLAIM} copied={false} onCopy={() => {}} />);
    expect(html).toContain("data-qr");
    expect(html).toContain("Download SVG");
    expect(html).toContain("The link and its QR code work like a password");
  });
});
