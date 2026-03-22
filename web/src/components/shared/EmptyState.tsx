import type { ReactNode } from "react";

interface EmptyStateProps {
  icon?: ReactNode;
  heading: string;
  description?: string;
  action?: ReactNode;
  role?: string;
}

export function EmptyState({
  icon,
  heading,
  description,
  action,
  role,
}: EmptyStateProps) {
  return (
    <div
      role={role}
      className="flex flex-col items-center justify-center gap-4 rounded-lg border border-border-muted bg-surface-sunken px-8 py-16 text-center"
    >
      {icon && (
        <span
          className="flex size-20 items-center justify-center rounded-full bg-surface-elevated text-text-faint"
          aria-hidden="true"
        >
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
