/**
 * Wires the workflow-schedule Orval hooks to the presentational SchedulePicker.
 *
 * Owns the data flow for one workflow's schedule: fetch (404 ⇒ "not scheduled"),
 * upsert / toggle / delete mutations, and cache invalidation of both this
 * workflow's schedule query and the global `/schedules` list (which powers the
 * workflow-list "Next run" column). The presentational picker stays target-
 * agnostic so the same component can serve sync schedules later.
 */

import { useQueryClient } from "@tanstack/react-query";

import type {
  ScheduleToggleRequest,
  ScheduleUpsertRequest,
} from "#/api/generated/model";
import { getListSchedulesApiV1SchedulesGetQueryKey } from "#/api/generated/schedules/schedules";
import {
  getGetWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleGetQueryKey,
  useDeleteWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleDelete,
  useGetWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleGet,
  useToggleWorkflowScheduleApiV1WorkflowsWorkflowIdSchedulePatch,
  useUpsertWorkflowScheduleApiV1WorkflowsWorkflowIdSchedulePut,
} from "#/api/generated/workflows/workflows";
import { STALE } from "#/api/query-client";
import { SchedulePicker } from "#/components/shared/SchedulePicker";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { toasts } from "#/lib/toasts";

interface WorkflowScheduleCardProps {
  workflowId: string;
}

export function WorkflowScheduleCard({
  workflowId,
}: WorkflowScheduleCardProps) {
  const queryClient = useQueryClient();

  // 404 is the expected "no schedule" state — don't retry it.
  const { data, isLoading } =
    useGetWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleGet(workflowId, {
      query: { staleTime: STALE.SLOW, retry: false },
    });
  const schedule = data?.status === 200 ? data.data : null;

  function invalidate() {
    void queryClient.invalidateQueries({
      queryKey:
        getGetWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleGetQueryKey(
          workflowId,
        ),
    });
    void queryClient.invalidateQueries({
      queryKey: getListSchedulesApiV1SchedulesGetQueryKey(),
    });
  }

  const upsert = useUpsertWorkflowScheduleApiV1WorkflowsWorkflowIdSchedulePut({
    mutation: {
      onSuccess: () => {
        invalidate();
        toasts.success("Schedule saved");
      },
      meta: { errorLabel: "Failed to save schedule" },
    },
  });

  const toggle = useToggleWorkflowScheduleApiV1WorkflowsWorkflowIdSchedulePatch(
    {
      mutation: {
        onSuccess: () => invalidate(),
        meta: { errorLabel: "Failed to update schedule" },
      },
    },
  );

  const remove =
    useDeleteWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleDelete({
      mutation: {
        onSuccess: () => {
          invalidate();
          toasts.success("Schedule removed");
        },
        meta: { errorLabel: "Failed to remove schedule" },
      },
    });

  const isPending = upsert.isPending || toggle.isPending || remove.isPending;

  return (
    <section className="mt-8 space-y-3">
      <SectionHeader title="Schedule" />
      {isLoading ? (
        <div className="h-24 rounded-lg border-l-2 border-border-muted bg-surface-elevated/30" />
      ) : (
        <SchedulePicker
          schedule={schedule}
          isPending={isPending}
          onSave={(req: ScheduleUpsertRequest) =>
            upsert.mutate({ workflowId, data: req })
          }
          onToggle={(enabled: boolean) =>
            toggle.mutate({
              workflowId,
              data: { enabled } satisfies ScheduleToggleRequest,
            })
          }
          onRemove={() => remove.mutate({ workflowId })}
        />
      )}
    </section>
  );
}
