/**
 * CSS-first animated presence hook — no library dependency.
 *
 * Manages mount/unmount lifecycle so CSS exit animations can complete
 * before the element is removed from the DOM.
 */

import { useCallback, useEffect, useRef, useState } from "react";

interface AnimatedPresenceResult {
  /** Whether the element should be in the DOM. */
  shouldRender: boolean;
  /** Ref to attach to the animated element (listens for animationend). */
  ref: React.RefObject<HTMLDivElement | null>;
  /** Current animation state — use for className switching. */
  state: "open" | "closed";
}

export function useAnimatedPresence(isOpen: boolean): AnimatedPresenceResult {
  const [shouldRender, setShouldRender] = useState(isOpen);
  const [state, setState] = useState<"open" | "closed">(
    isOpen ? "open" : "closed",
  );
  const ref = useRef<HTMLDivElement>(null);
  const shouldRenderRef = useRef(shouldRender);
  shouldRenderRef.current = shouldRender;

  const handleAnimationEnd = useCallback(() => {
    if (state === "closed") {
      setShouldRender(false);
    }
  }, [state]);

  useEffect(() => {
    if (isOpen) {
      setShouldRender(true);
      setState("open");
    } else if (shouldRenderRef.current) {
      setState("closed");
    }
  }, [isOpen]);

  // Listen for animationend to unmount after exit animation
  useEffect(() => {
    const el = ref.current;
    if (!el || state !== "closed") return;

    el.addEventListener("animationend", handleAnimationEnd);
    return () => el.removeEventListener("animationend", handleAnimationEnd);
  }, [state, handleAnimationEnd]);

  return { shouldRender, ref, state };
}
