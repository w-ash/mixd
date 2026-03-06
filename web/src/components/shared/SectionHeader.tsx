export function SectionHeader({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div>
      <h2 className="font-display text-xs font-medium uppercase tracking-wider text-text-muted border-l-2 border-primary/40 pl-3">
        {title}
      </h2>
      <p className="mt-1 text-sm text-text-faint">{description}</p>
    </div>
  );
}
