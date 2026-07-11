import { useEffect, useRef } from "react";

/**
 * Register a global keyboard shortcut.
 *
 * `keys` is an array like `["cmd", "k"]`. "cmd" matches Meta on Mac, Ctrl
 * elsewhere. Latest `keys`/`callback` are held in refs so re-renders don't
 * rebind the window listener — only `enabled` toggles it.
 */
export function useKeyboardShortcut(
  keys: string[],
  callback: () => void,
  enabled = true,
) {
  const keysRef = useRef(keys);
  keysRef.current = keys;
  const callbackRef = useRef(callback);
  callbackRef.current = callback;

  useEffect(() => {
    if (!enabled) return;

    function handler(e: KeyboardEvent) {
      const match = keysRef.current.every((key) => {
        if (key === "cmd" || key === "ctrl") {
          return e.metaKey || e.ctrlKey;
        }
        return e.key.toLowerCase() === key.toLowerCase();
      });
      if (match) {
        e.preventDefault();
        callbackRef.current();
      }
    }

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [enabled]);
}
