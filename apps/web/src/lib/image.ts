// On-device photo compression for the portal (02-ui.md section 3: under 1.5 MB before upload). The sizing rules
// are pure and tested; the canvas work runs only in the browser.

export const MAX_UPLOAD_BYTES = 1_500_000;
export const MAX_EDGE_PX = 2000;
export const ACCEPTED_TYPES = ["image/jpeg", "image/png"] as const;
const QUALITIES = [0.85, 0.75, 0.65, 0.55, 0.45] as const;

export function isAcceptedType(type: string): boolean {
  return (ACCEPTED_TYPES as readonly string[]).includes(type);
}

/** Scale (w, h) to fit inside max on the longer edge, never upscaling. */
export function fitWithin(w: number, h: number, max = MAX_EDGE_PX): { width: number; height: number } {
  if (w <= 0 || h <= 0) return { width: 0, height: 0 };
  const scale = Math.min(1, max / Math.max(w, h));
  return { width: Math.max(1, Math.round(w * scale)), height: Math.max(1, Math.round(h * scale)) };
}

export function needsCompression(bytes: number, w: number, h: number): boolean {
  return bytes > MAX_UPLOAD_BYTES || Math.max(w, h) > MAX_EDGE_PX;
}

export class PhotoError extends Error {}

/** Returns a JPEG under MAX_UPLOAD_BYTES (or the original file when it already fits). Browser only. */
export async function compressPhoto(file: File): Promise<Blob> {
  if (!isAcceptedType(file.type)) throw new PhotoError("Choose a JPEG or PNG photo.");
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    throw new PhotoError("This photo could not be opened. Try taking it again.");
  }
  try {
    if (!needsCompression(file.size, bitmap.width, bitmap.height)) return file;
    const { width, height } = fitWithin(bitmap.width, bitmap.height);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new PhotoError("This device could not prepare the photo.");
    ctx.drawImage(bitmap, 0, 0, width, height);
    for (const q of QUALITIES) {
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", q));
      if (blob && blob.size <= MAX_UPLOAD_BYTES) return blob;
    }
    throw new PhotoError("This photo is too large even after shrinking it. Try a closer, simpler shot.");
  } finally {
    bitmap.close();
  }
}
