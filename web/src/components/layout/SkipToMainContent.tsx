/**
 * Keyboard-accessible "skip to content" link that becomes visible only
 * on focus. Mounted by both layout shells so the affordance is present
 * across mobile and desktop.
 */
export function SkipToMainContent() {
  return (
    <a
      href="#main-content"
      className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-4 focus:py-2 focus:text-surface focus:outline-none"
    >
      Skip to content
    </a>
  );
}
