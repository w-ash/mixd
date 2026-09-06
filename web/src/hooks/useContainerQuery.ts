/**
 * Whether an element is at least `minWidthPx` wide.
 *
 * A container query in JavaScript: it answers about the element's own box, not
 * the viewport, so a layout can pick a rendering (table vs cards) for the space
 * it actually has — inside a sidebar, a dialog, or a split pane — and render
 * only that branch instead of mounting both and hiding one with CSS.
 *
 * The first render guesses from the document width, so a desktop viewer's
 * wide branch is the one that mounts. The real measurement lands in a layout
 * effect and is authoritative — it is applied before the browser paints, so a
 * wrong guess costs a re-render, never a visible jump. Attach the ref to an
 * element that is always rendered: measurement starts when the layout effect
 * first runs, and an element that arrives later is not picked up.
 */

import { type RefObject, useLayoutEffect, useState } from "react";

export function useContainerQuery(
  ref: RefObject<HTMLElement | null>,
  minWidthPx: number,
): boolean {
  const [matches, setMatches] = useState(
    // The element has no box yet; the document is the closest upper bound.
    () =>
      typeof document !== "undefined" &&
      document.documentElement.clientWidth >= minWidthPx,
  );

  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;

    const apply = (width: number) => setMatches(width >= minWidthPx);
    apply(element.getBoundingClientRect().width);

    const observer = new ResizeObserver((entries) => {
      const entry = entries.at(-1);
      if (entry) apply(entry.contentRect.width);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref, minWidthPx]);

  return matches;
}
