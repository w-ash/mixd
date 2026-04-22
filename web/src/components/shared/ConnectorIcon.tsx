import { connectorBrand } from "#/lib/connector-brand";

interface ConnectorIconProps {
  /** Backend-sourced connector identifier, e.g. ``"spotify"``. */
  name: string;
  className?: string;
  /** Hide the text label — show icon only. */
  labelHidden?: boolean;
}

export function ConnectorIcon({
  name,
  className = "",
  labelHidden = false,
}: ConnectorIconProps) {
  const brand = connectorBrand[name];
  if (!brand) return null;

  return (
    <span
      className={`inline-flex items-center gap-2 font-display text-sm font-medium ${brand.textColor} ${className}`}
      {...(labelHidden ? { "aria-hidden": true } : { title: brand.label })}
    >
      <brand.logo className="size-4 shrink-0" />
      {!labelHidden && brand.label}
    </span>
  );
}
