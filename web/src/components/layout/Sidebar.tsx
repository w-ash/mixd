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

import { useHealthCheckApiV1HealthGet } from "@/api/generated/health/health";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { MixdLogo } from "@/components/shared/MixdLogo";
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
  const { data: healthData } = useHealthCheckApiV1HealthGet({
    query: { staleTime: Infinity },
  });
  const version =
    healthData?.status === 200 &&
    healthData.data &&
    typeof healthData.data === "object" &&
    "version" in healthData.data
      ? String((healthData.data as { version: string }).version)
      : null;

  return (
    <nav
      className="sticky top-0 flex h-screen w-56 shrink-0 flex-col border-r border-border bg-surface-sunken"
      aria-label="Main navigation"
    >
      {/* Brand masthead */}
      <div className="flex h-28 flex-col items-center justify-center gap-2 border-b border-border">
        <MixdLogo />
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
      <div className="flex items-center justify-between px-5 py-4">
        <span className="font-mono text-xs text-text-faint">
          {version ? `v${version}` : ""}
        </span>
        <ThemeToggle />
      </div>
    </nav>
  );
}
