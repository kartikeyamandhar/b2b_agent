"use client";

import { useMemo, useState } from "react";

import { shortId } from "@/lib/claims";
import { qrMatrix, qrPath, qrPixels, qrRects, qrSheetHtml, qrSpan, qrSvg, type QrItem } from "@/lib/qr";

const BUTTON = "rounded border border-line bg-surface px-2 py-1 text-xs text-ink hover:bg-panel";

/** A claim link as a QR code. Dark on white in both themes (phones read it from the screen or from paper) and drawn at
 *  a whole number of pixels per module, so it stays sharp. */
export function QrCode({ value, label, size = 168 }: { value: string; label: string; size?: number }) {
  const drawn = useMemo(() => {
    try {
      const m = qrMatrix(value);
      return { span: qrSpan(m), d: qrPath(m), px: qrPixels(m, size) };
    } catch {
      return null;
    }
  }, [value, size]);
  if (!drawn) return <p className="text-xs text-warn-ink">This link cannot be drawn as a QR code.</p>;
  return (
    <svg
      role="img"
      aria-label={label}
      width={drawn.px}
      height={drawn.px}
      viewBox={`0 0 ${drawn.span} ${drawn.span}`}
      shapeRendering="crispEdges"
      className="block h-auto max-w-full"
      data-qr
    >
      <rect width={drawn.span} height={drawn.span} fill="#fff" />
      <path d={drawn.d} fill="#000" />
    </svg>
  );
}

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** The code as a PNG, 16 pixels per module (about 900 px square), for tools that take no SVG. */
export function qrPngBlob(value: string, scale = 16): Promise<Blob> {
  const m = qrMatrix(value);
  const side = qrSpan(m) * scale;
  const canvas = document.createElement("canvas");
  canvas.width = side;
  canvas.height = side;
  const ctx = canvas.getContext("2d");
  if (!ctx) return Promise.reject(new Error("no canvas"));
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, side, side);
  ctx.fillStyle = "#000";
  for (const [x, y, w, h] of qrRects(m, scale)) ctx.fillRect(x, y, w, h);
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("no image"))), "image/png"));
}

/** A printable sheet of codes in a new window (the links never go into a URL or storage). False when pop-ups are
 *  blocked. The new window gets no handle back to the console. */
export function openQrSheet(items: readonly QrItem[], heading: string, open: (url: string, target: string) => Window | null = (u, t) => window.open(u, t)): boolean {
  const w = open("", "_blank");
  if (!w) return false;
  w.opener = null;
  w.document.open();
  w.document.write(qrSheetHtml(items, heading));
  w.document.close();
  w.focus();
  w.print();
  return true;
}

/** One claim's code with its downloads. */
export function QrPanel({ url, claimId }: { url: string; claimId: string }) {
  const [problem, setProblem] = useState<string | null>(null);
  const name = `claim-${shortId(claimId)}-qr`;
  return (
    <div className="grid justify-items-start gap-2" data-qr-panel>
      <div className="rounded-md border border-line bg-white p-2">
        <QrCode value={url} label={`QR code for the link of claim ${shortId(claimId)}`} />
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className={BUTTON} onClick={() => saveBlob(new Blob([qrSvg(url, 1024)], { type: "image/svg+xml" }), `${name}.svg`)}>
          Download SVG
        </button>
        <button
          type="button"
          className={BUTTON}
          onClick={() => {
            setProblem(null);
            qrPngBlob(url)
              .then((png) => saveBlob(png, `${name}.png`))
              .catch(() => setProblem("This browser could not draw the PNG. Download the SVG instead."));
          }}
        >
          Download PNG
        </button>
      </div>
      {problem ? (
        <p role="alert" className="text-xs text-warn-ink">
          {problem}
        </p>
      ) : null}
    </div>
  );
}
