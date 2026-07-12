import {
  CircleUser,
  History,
  ListMusic,
  type LucideIcon,
  Plug,
  RefreshCw,
  Settings as SettingsIcon,
  Sparkles,
  Tag,
} from "lucide-react";
import { NavLink } from "react-router";

import { authEnabled } from "#/api/auth";
import { ThemeToggle } from "#/components/layout/ThemeToggle";
import { Sheet } from "#/components/ui/sheet";
import { cn } from "#/lib/utils";

const linkClass =
  "flex items-center gap-3 rounded-md px-3 py-3 font-display text-sm transition-colors min-h-[44px]";
const activeClass = "bg-surface text-primary";
const inactiveClass = "text-text hover:bg-surface";

interface SheetLink {
  to: string;
  label: string;
  Icon: LucideIcon;
}

const SHEET_LINKS: readonly SheetLink[] = [
  { to: "/playlists", label: "Playlists", Icon: ListMusic },
  { to: "/settings/integrations", label: "Integrations", Icon: Plug },
  { to: "/settings/assistant", label: "Assistant", Icon: Sparkles },
  { to: "/settings/sync", label: "Sync", Icon: RefreshCw },
  { to: "/settings/tags", label: "Tags", Icon: Tag },
  { to: "/settings/imports", label: "Import History", Icon: History },
  ...(authEnabled
    ? [{ to: "/settings/account", label: "Account", Icon: CircleUser }]
    : []),
];

interface MobileMoreSheetProps {
  open: boolean;
  onClose: () => void;
}

export function MobileMoreSheet({ open, onClose }: MobileMoreSheetProps) {
  return (
    <Sheet open={open} onClose={onClose} ariaLabel="More navigation">
      <div className="flex flex-col gap-0.5">
        {SHEET_LINKS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            onClick={onClose}
            className={({ isActive }) =>
              cn(linkClass, isActive ? activeClass : inactiveClass)
            }
          >
            <Icon
              strokeWidth={1.5}
              className="size-[18px]"
              aria-hidden="true"
            />
            {label}
          </NavLink>
        ))}
      </div>

      <div className="mt-3 flex items-center justify-between border-t border-border px-3 pt-3">
        <NavLink
          to="/settings"
          onClick={onClose}
          className="font-display text-xs uppercase tracking-wider text-text-faint hover:text-text"
        >
          <span className="inline-flex items-center gap-2">
            <SettingsIcon className="size-3.5" aria-hidden="true" />
            All settings
          </span>
        </NavLink>
        <ThemeToggle />
      </div>
    </Sheet>
  );
}
