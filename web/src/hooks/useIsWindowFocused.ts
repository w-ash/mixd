/**
 * Whether this window currently has focus.
 *
 * Exists because TanStack Query's own `focusManager` is visibility-based in v5
 * (`isFocused()` is really "not `document.hidden`"), which cannot tell a tab the
 * user is looking at from one sitting visible on a second monitor, untouched all
 * day. That distinction is what decides whether a background poll is serving a
 * present user or just billing serverless database time for nobody.
 *
 * `document.hasFocus()` is the real signal; `focus`/`blur` on the window are its
 * change events.
 */

import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("focus", onChange);
  window.addEventListener("blur", onChange);
  return () => {
    window.removeEventListener("focus", onChange);
    window.removeEventListener("blur", onChange);
  };
}

const getSnapshot = () => document.hasFocus();

/** Server render has no window; assume focused so the first paint isn't stalled. */
const getServerSnapshot = () => true;

export function useIsWindowFocused(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
