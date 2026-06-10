/**
 * Presentational shell around SchedulePicker: loading skeleton, then the picker.
 *
 * Heading-less on purpose — the consumer owns the surrounding heading (a full
 * SectionHeader on the workflow detail page, a lighter inline label inside a
 * sync card), so the same shell serves both. Pair with a controller from
 * `useScheduleController` for the data/mutation wiring.
 */

import { SchedulePicker } from "#/components/shared/SchedulePicker";
import type { ScheduleController } from "#/hooks/useScheduleController";

export function ScheduleCard({
  schedule,
  isLoading,
  isPending,
  onSave,
  onToggle,
  onRemove,
}: ScheduleController) {
  if (isLoading) {
    return (
      <div className="h-24 rounded-lg border-l-2 border-border-muted bg-surface-elevated/30" />
    );
  }

  return (
    <SchedulePicker
      schedule={schedule}
      isPending={isPending}
      onSave={onSave}
      onToggle={onToggle}
      onRemove={onRemove}
    />
  );
}
