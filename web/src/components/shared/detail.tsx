/**
 * The two stanzas every entity detail page is built from: a labelled metadata
 * field and a left-accented section card. Shared so a track and an artist read
 * as the same kind of page.
 */

import { cn } from "#/lib/utils";

/** Labelled metadata field — a `<dt>`/`<dd>` pair for a detail page's `<dl>`. */
export function DetailField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wider text-text-faint">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm text-text">{children}</dd>
    </div>
  );
}

/** Section card with heading. */
export function DetailSection({
  title,
  children,
  className,
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "rounded-lg border-l-2 border-primary/30 bg-surface-sunken p-5",
        className,
      )}
    >
      <h2 className="mb-3 font-display text-xs font-medium uppercase tracking-wider text-text-muted">
        {title}
      </h2>
      {children}
    </section>
  );
}
