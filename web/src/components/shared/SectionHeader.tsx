export function SectionHeader({
  title,
  description,
}: {
  title: string;
  description?: string;
}) {
  return (
    <div>
      <h2 className="font-display text-sm font-medium uppercase tracking-wider text-text-muted">
        {title}
      </h2>
      {description && (
        <p className="mt-1 text-xs text-text-faint">{description}</p>
      )}
    </div>
  );
}
