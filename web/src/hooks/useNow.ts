/**
 * Self-ticking "current time" hook.
 *
 * Returns Date.now() and re-renders the calling component on a fixed
 * interval. Use for relative-time displays ("12s ago") that need to
 * update independently of any data source. Scope it to the component
 * that renders the time, not to a parent context — otherwise the parent
 * re-renders all children every tick.
 *
 * Two ways to stop paying for a tick nobody reads: pass `intervalMs <= 0`
 * when the caller is not showing a relative time at all, and the hook pauses
 * itself while the tab is hidden, resuming with a fresh value.
 */

import { useEffect, useState } from "react";

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (intervalMs <= 0) return;

    let id: ReturnType<typeof setInterval> | undefined;
    const tick = () => setNow(Date.now());
    const stop = () => {
      clearInterval(id);
      id = undefined;
    };
    const start = () => {
      if (id === undefined) id = setInterval(tick, intervalMs);
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        stop();
      } else {
        // The displayed time is as stale as the tab was hidden.
        tick();
        start();
      }
    };

    if (document.visibilityState !== "hidden") start();
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [intervalMs]);

  return now;
}
