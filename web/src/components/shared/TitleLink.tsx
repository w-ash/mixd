import type { ReactNode } from "react";
import { Link } from "react-router";

/**
 * Truncating title link used as the headline of every list-row card
 * (TrackCard, PlaylistTrackCard, PlaylistCard, WorkflowCard). Centralizes
 * the type ramp + hover treatment so a design tweak hits one file.
 */
export interface TitleLinkProps {
  to: string;
  /** Forwards to react-router's view-transition handoff. */
  viewTransition?: boolean;
  children: ReactNode;
}

export function TitleLink({ to, viewTransition, children }: TitleLinkProps) {
  return (
    <Link
      to={to}
      viewTransition={viewTransition}
      className="block truncate font-display text-sm font-medium text-text transition-colors hover:text-primary"
    >
      {children}
    </Link>
  );
}
