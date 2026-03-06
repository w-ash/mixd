import type { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  description?: string;
  action?: ReactNode;
}

export function PageHeader({ title, description, action }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between gap-4 pb-6 border-b border-border-muted mb-8">
      <div>
        <h1 className="font-display text-3xl font-semibold tracking-tight">
          {title}
        </h1>
        {description && <p className="mt-1 text-text-muted">{description}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}
