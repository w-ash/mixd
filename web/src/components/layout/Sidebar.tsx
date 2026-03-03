import { NavLink } from "react-router";

const navItems = [
  { to: "/", label: "Dashboard", icon: "◆" },
  { to: "/playlists", label: "Playlists", icon: "♫" },
  { to: "/settings", label: "Settings", icon: "⚙" },
] as const;

export function Sidebar() {
  return (
    <nav
      className="flex h-screen w-56 shrink-0 flex-col border-r border-border bg-surface-sunken"
      aria-label="Main navigation"
    >
      <div className="flex h-14 items-center px-5">
        <span className="font-display text-lg font-semibold tracking-tight text-primary">
          narada
        </span>
      </div>

      <ul className="mt-2 flex flex-1 flex-col gap-0.5 px-2">
        {navItems.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors duration-150 ${
                  isActive
                    ? "bg-surface-elevated text-primary"
                    : "text-text-muted hover:bg-surface-elevated hover:text-text"
                }`
              }
            >
              <span className="text-base" aria-hidden="true">
                {item.icon}
              </span>
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
