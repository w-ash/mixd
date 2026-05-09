import {
  GitBranch,
  LayoutDashboard,
  Library,
  type LucideIcon,
  MoreHorizontal,
} from "lucide-react";
import { useState } from "react";
import { NavLink } from "react-router";

import { MobileMoreSheet } from "#/components/layout/MobileMoreSheet";
import { cn } from "#/lib/utils";

interface PrimaryRoute {
  to: string;
  label: string;
  Icon: LucideIcon;
  end?: boolean;
}

const PRIMARY_ROUTES: readonly PrimaryRoute[] = [
  { to: "/", label: "Home", Icon: LayoutDashboard, end: true },
  { to: "/library", label: "Library", Icon: Library },
  { to: "/workflows", label: "Workflows", Icon: GitBranch },
];

const tabClass =
  "relative flex flex-1 flex-col items-center justify-center gap-1 px-2 py-2 font-display text-[11px] transition-colors min-h-[56px]";
const activeClass =
  "text-primary before:absolute before:inset-x-6 before:top-0 before:h-0.5 before:rounded-full before:bg-primary";
const inactiveClass = "text-text-muted hover:text-text";

export function MobileBottomNav() {
  const [moreOpen, setMoreOpen] = useState(false);

  return (
    <>
      <nav
        aria-label="Mobile navigation"
        className="fixed bottom-0 left-0 right-0 z-40 border-t border-border bg-surface-sunken pb-safe lg:hidden"
      >
        <div className="flex items-stretch">
          {PRIMARY_ROUTES.map(({ to, label, Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(tabClass, isActive ? activeClass : inactiveClass)
              }
            >
              <Icon strokeWidth={1.5} className="size-5" aria-hidden="true" />
              {label}
            </NavLink>
          ))}
          <button
            type="button"
            onClick={() => setMoreOpen(true)}
            aria-label="Open more navigation"
            aria-expanded={moreOpen}
            className={cn(tabClass, inactiveClass)}
          >
            <MoreHorizontal
              strokeWidth={1.5}
              className="size-5"
              aria-hidden="true"
            />
            More
          </button>
        </div>
      </nav>

      <MobileMoreSheet open={moreOpen} onClose={() => setMoreOpen(false)} />
    </>
  );
}
