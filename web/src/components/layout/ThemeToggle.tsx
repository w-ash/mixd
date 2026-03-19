import { Monitor, Moon, Sun } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useTheme } from "@/contexts/ThemeContext";

const modes = ["dark", "light", "system"] as const;

const modeConfig = {
  dark: { Icon: Moon, label: "Switch to light mode" },
  light: { Icon: Sun, label: "Switch to system theme" },
  system: { Icon: Monitor, label: "Switch to dark mode" },
} as const;

export function ThemeToggle() {
  const { mode, setMode } = useTheme();
  const { Icon, label } = modeConfig[mode];

  function cycle() {
    const next = modes[(modes.indexOf(mode) + 1) % modes.length];
    setMode(next);
  }

  return (
    <Button
      variant="ghost"
      size="icon-xs"
      onClick={cycle}
      aria-label={label}
      className="text-text-faint hover:text-text"
    >
      <Icon />
    </Button>
  );
}
