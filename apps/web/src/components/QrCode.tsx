"use client";

import { useMemo, useState } from "react";

import { shortId } from "@/lib/claims";
import { qrMatrix, qrPath, qrPixels, qrRects, qrSpan, qrSvg } from "@/lib/qr";

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

type Opener = (url: string, target: string) => Window | null;

/** A self-contained printable page (the product's poster) in a new window, printed at once; the link never goes into
 *  a URL or storage. False when pop-ups are blocked. The new window gets no handle back to the console. */
export function openPrintable(html: string, open: Opener = (u, t) => window.open(u, t)): boolean {
  const w = open("", "_blank");
  if (!w) return false;
  w.opener = null;
  w.document.open();
  w.document.write(html);
  w.document.close();
  w.focus();
  w.print();
  return true;
}

/** A code with its SVG and PNG downloads: one claim's link (named after the claim), or a product's claim link with its
 *  own file name and label. */
export function QrPanel({ url, claimId, fileName, label }: { url: string; claimId?: string; fileName?: string; label?: string }) {
  const [problem, setProblem] = useState<string | null>(null);
  const name = fileName ?? (claimId ? `claim-${shortId(claimId)}-qr` : "claim-link-qr");
  return (
    <div className="grid justify-items-start gap-2" data-qr-panel>
      <div className="rounded-md border border-line bg-white p-2">
        <QrCode value={url} label={label ?? (claimId ? `QR code for the link of claim ${shortId(claimId)}` : "QR code for the claim link")} />
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
