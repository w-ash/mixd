import type { ReactNode } from "react";

interface EmptyStateProps {
  icon?: string;
  heading: string;
  description?: string;
  action?: ReactNode;
}

export function EmptyState({
  icon,
  heading,
  description,
  action,
}: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-border-muted bg-surface-sunken px-8 py-16 text-center">
      {icon && (
        <span className="text-4xl text-text-faint" aria-hidden="true">
          {icon}
        </span>
      )}
      <h2 className="font-display text-lg font-medium text-text">{heading}</h2>
      {description && (
        <p className="max-w-sm text-sm text-text-muted">{description}</p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}
