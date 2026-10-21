/** The RecallOps mark from the README banner (docs/assets/banner.svg): a shield with a check in a soft rounded
 *  square, drawn in the brand blue so it follows light and dark mode. Decorative; the wordmark text names it. */
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 56 56" className="shrink-0 text-brand" fill="none">
      <rect x="0.75" y="0.75" width="54.5" height="54.5" rx="14" fill="currentColor" fillOpacity="0.1" stroke="currentColor" strokeWidth="1.5" />
      <path d="M28 13 L41 18 V29 C41 38.5 35.5 44 28 47 C20.5 44 15 38.5 15 29 V18 Z" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round" />
      <path d="M22 30 L26.5 34.5 L34.5 25.5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
