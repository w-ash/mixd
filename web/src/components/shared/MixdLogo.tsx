import { useId } from "react";

import { cn } from "#/lib/utils";

interface MixdLogoProps {
  size?: "sm" | "lg";
}

export function MixdLogo({ size = "sm" }: MixdLogoProps) {
  const sheenId = useId();
  const svgClass = cn(size === "lg" ? "size-16" : "size-12", "shrink-0");
  const textClass = cn(
    "font-display tracking-[0.3em] pl-[0.3em] uppercase text-text-muted",
    size === "lg" ? "text-base" : "text-sm",
  );

  return (
    <>
      <svg viewBox="0 0 128 128" className={svgClass} aria-hidden="true">
        <defs>
          <radialGradient id={sheenId} cx="38%" cy="36%" r="50%">
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
        <circle cx="64" cy="64" r="50" fill={`url(#${sheenId})`} />
      </svg>
      <span className={textClass}>mixd</span>
    </>
  );
}
