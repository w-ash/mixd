import type { LucideIcon } from "lucide-react";
import {
  ChevronRight,
  GitBranch,
  LayoutDashboard,
  Library,
  ListMusic,
  Plug,
  RefreshCw,
  Settings,
} from "lucide-react";
import { NavLink, useLocation } from "react-router";

import { cn } from "@/lib/utils";

interface NavChild {
  to: string;
  label: string;
  Icon: LucideIcon;
}

interface NavItem {
  to: string;
  label: string;
  Icon: LucideIcon;
  end?: boolean;
  children?: NavChild[];
}

const navItems: NavItem[] = [
  { to: "/", label: "Dashboard", Icon: LayoutDashboard, end: true },
  { to: "/library", label: "Library", Icon: Library },
  { to: "/playlists", label: "Playlists", Icon: ListMusic },
  { to: "/workflows", label: "Workflows", Icon: GitBranch },
  {
    to: "/settings",
    label: "Settings",
    Icon: Settings,
    children: [
      { to: "/settings/integrations", label: "Integrations", Icon: Plug },
      { to: "/settings/sync", label: "Sync", Icon: RefreshCw },
    ],
  },
];

const linkClass =
  "relative flex items-center gap-3 rounded-md px-3 py-2.5 font-display text-sm transition-colors duration-150";
const activeClass =
  "bg-surface-elevated text-primary before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-0.5 before:rounded-full before:bg-primary";
const inactiveClass =
  "text-text-muted hover:bg-surface-elevated hover:text-text";

export function Sidebar() {
  const { pathname } = useLocation();

  return (
    <nav
      className="sticky top-0 flex h-screen w-56 shrink-0 flex-col border-r border-border bg-surface-sunken"
      aria-label="Main navigation"
    >
      {/* Brand masthead */}
      <div className="flex h-28 flex-col items-center justify-center gap-2 border-b border-border">
        <svg
          viewBox="0 0 128 128"
          className="size-12 shrink-0"
          aria-hidden="true"
        >
          <defs>
            <radialGradient id="sheen" cx="38%" cy="36%" r="50%">
              <stop offset="0%" stopColor="white" stopOpacity={0.12} />
              <stop offset="100%" stopColor="white" stopOpacity={0} />
            </radialGradient>
          </defs>
          <circle cx="64" cy="64" r="52" fill="#9E7B1F" />
          <circle cx="64" cy="64" r="50" fill="#C59A2B" />
          <circle
            cx="64"
            cy="64"
            r="46"
            fill="none"
            stroke="#A88220"
            strokeWidth="0.8"
          />
          <circle
            cx="64"
            cy="64"
            r="43"
            fill="none"
            stroke="#A88220"
            strokeWidth="1.2"
          />
          <circle
            cx="64"
            cy="64"
            r="40"
            fill="none"
            stroke="#A88220"
            strokeWidth="0.8"
          />
          <circle
            cx="64"
            cy="64"
            r="37"
            fill="none"
            stroke="#A88220"
            strokeWidth="1.0"
          />
          <circle
            cx="64"
            cy="64"
            r="34"
            fill="none"
            stroke="#A88220"
            strokeWidth="0.8"
          />
          <circle
            cx="64"
            cy="64"
            r="31"
            fill="none"
            stroke="#A88220"
            strokeWidth="1.2"
          />
          <circle
            cx="64"
            cy="64"
            r="28"
            fill="none"
            stroke="#A88220"
            strokeWidth="0.8"
          />
          <circle
            cx="64"
            cy="64"
            r="22"
            fill="none"
            stroke="#9E7B1F"
            strokeWidth="1.5"
          />
          <circle cx="64" cy="64" r="20" fill="#D4AC35" />
          <circle cx="64" cy="64" r="7" className="fill-surface-sunken" />
          <circle cx="64" cy="64" r="50" fill="url(#sheen)" />
        </svg>
        <span className="font-display text-xs tracking-[0.25em] uppercase text-text-muted">
          narada
        </span>
      </div>

      {/* Navigation */}
      <ul className="mt-4 flex flex-1 flex-col gap-1 px-2">
        {navItems.map((item) =>
          item.children ? (
            <li key={item.to}>
              {/* Parent — clickable, navigates to default sub-page */}
              <NavLink
                to={item.children[0].to}
                className={cn(
                  linkClass,
                  pathname.startsWith(item.to) ? activeClass : inactiveClass,
                )}
              >
                <item.Icon
                  size={18}
                  strokeWidth={1.5}
                  className="shrink-0"
                  aria-hidden="true"
                />
                {item.label}
                <ChevronRight
                  size={14}
                  className={cn(
                    "ml-auto transition-transform duration-150",
                    pathname.startsWith(item.to) && "rotate-90",
                  )}
                />
              </NavLink>
              {/* Animated child list — grid controls height transition */}
              <div
                className={cn(
                  "grid transition-[grid-template-rows] duration-150",
                  pathname.startsWith(item.to)
                    ? "grid-rows-[1fr]"
                    : "grid-rows-[0fr]",
                )}
              >
                <ul className="overflow-hidden mt-0.5 flex flex-col gap-0.5">
                  {item.children.map((child) => (
                    <li key={child.to}>
                      <NavLink
                        to={child.to}
                        className={({ isActive }) =>
                          cn(
                            linkClass,
                            "py-2 pl-9",
                            isActive ? activeClass : inactiveClass,
                          )
                        }
                      >
                        <child.Icon
                          size={16}
                          strokeWidth={1.5}
                          className="shrink-0"
                          aria-hidden="true"
                        />
                        {child.label}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            </li>
          ) : (
            <li key={item.to}>
              <NavLink
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  cn(linkClass, isActive ? activeClass : inactiveClass)
                }
              >
                <item.Icon
                  size={18}
                  strokeWidth={1.5}
                  className="shrink-0"
                  aria-hidden="true"
                />
                {item.label}
              </NavLink>
            </li>
          ),
        )}
      </ul>

      {/* Footer */}
      <div className="px-5 py-4">
        <span className="font-mono text-xs text-text-faint">v0.4</span>
      </div>
    </nav>
  );
}
