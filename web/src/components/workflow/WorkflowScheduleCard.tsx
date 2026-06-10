/**
 * The "Schedule" section on a workflow's detail page.
 *
 * Thin consumer of the shared schedule shell: `useWorkflowScheduleController`
 * owns the fetch/mutate/invalidate wiring, `ScheduleCard` renders it. The sync
 * cards on the Sync page reuse the same controller+card with the sync binding.
 */

import { ScheduleCard } from "#/components/shared/ScheduleCard";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { useWorkflowScheduleController } from "#/hooks/useScheduleController";

interface WorkflowScheduleCardProps {
  workflowId: string;
}

export function WorkflowScheduleCard({
  workflowId,
}: WorkflowScheduleCardProps) {
  const controller = useWorkflowScheduleController(workflowId);

  return (
    <section className="mt-8 space-y-3">
      <SectionHeader title="Schedule" />
      <ScheduleCard {...controller} />
    </section>
  );
}
