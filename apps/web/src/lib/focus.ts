// Focus for inline confirmations: the button that opened one is often hidden or far away once it shows, so keyboard
// and screen-reader users would not know it opened.

/** A callback ref that focuses its element when it appears. It is one stable function, so React calls it when the
 *  element mounts (and with null when it goes), never on a re-render. The element needs tabIndex -1 unless it is a
 *  control. */
export function focusOnOpen(el: HTMLElement | null): void {
  el?.focus();
}
