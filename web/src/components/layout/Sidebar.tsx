import {
  ArrowDownToLine,
  LayoutDashboard,
  Library,
  ListMusic,
  Settings,
} from "lucide-react";
import { NavLink } from "react-router";

import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", label: "Dashboard", Icon: LayoutDashboard },
  { to: "/imports", label: "Imports", Icon: ArrowDownToLine },
  { to: "/playlists", label: "Playlists", Icon: ListMusic },
  { to: "/library", label: "Library", Icon: Library },
  { to: "/settings", label: "Settings", Icon: Settings },
] as const;

export function Sidebar() {
  return (
    <nav
      className="sticky top-0 flex h-screen w-56 shrink-0 flex-col border-r border-border bg-surface-sunken"
      aria-label="Main navigation"
    >
      {/* Brand */}
      <div className="flex h-16 items-center border-b border-border px-5">
        <span className="font-display text-lg font-semibold tracking-tight text-primary">
          narada
        </span>
      </div>

      {/* Navigation */}
      <ul className="mt-4 flex flex-1 flex-col gap-1 px-2">
        {navItems.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                cn(
                  "relative flex items-center gap-3 rounded-md px-3 py-2.5 font-display text-sm transition-colors duration-150",
                  isActive
                    ? "bg-surface-elevated text-primary before:absolute before:left-0 before:top-1.5 before:bottom-1.5 before:w-0.5 before:rounded-full before:bg-primary"
                    : "text-text-muted hover:bg-surface-elevated hover:text-text",
                )
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
        ))}
      </ul>

      {/* Footer */}
      <div className="px-5 py-4">
        <span className="font-mono text-xs text-text-faint">v0.3</span>
      </div>
    </nav>
  );
}
