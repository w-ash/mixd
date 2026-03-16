import { ArrowLeft } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

interface BackLinkProps {
  to: string;
  children: ReactNode;
}

/**
 * Consistent back-navigation link used on detail pages.
 * Renders an ArrowLeft icon + label, positioned above PageHeader.
 */
export function BackLink({ to, children }: BackLinkProps) {
  return (
    <Link
      to={to}
      className="mb-4 inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text transition-colors"
    >
      <ArrowLeft size={14} />
      {children}
    </Link>
  );
}
