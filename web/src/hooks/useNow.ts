/**
 * Self-ticking "current time" hook.
 *
 * Returns Date.now() and re-renders the calling component on a fixed
 * interval. Use for relative-time displays ("12s ago") that need to
 * update independently of any data source. Scope it to the component
 * that renders the time, not to a parent context — otherwise the parent
 * re-renders all children every tick.
 */

import { useEffect, useState } from "react";

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);

  return now;
}
