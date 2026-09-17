/** Browser download helper.
 *
 * Revoking an object URL in the same tick as `a.click()` cancels the download
 * the click just started — Chrome leaves an unfinished `.crdownload` of
 * exactly the right length behind. The URL must outlive the browser's handoff,
 * so it is revoked on a timer instead. */

/** How long a blob URL stays alive after the click that consumed it. */
export const REVOKE_DELAY_MS = 10_000;

export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
}

export function saveText(text: string, filename: string, mime = 'text/markdown'): void {
  saveBlob(new Blob([text], { type: mime }), filename);
}
