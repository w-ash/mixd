/**
 * Schedule-card controllers for the two kinds of schedulable target.
 *
 * Both a workflow schedule and a sync schedule need the identical wiring —
 * fetch (404 ⇒ "not scheduled"), upsert / toggle / delete mutations, cache
 * invalidation of the target's own query plus the global `/schedules` list, and
 * save/remove toasts. The only thing that differs is which Orval hook family is
 * called, so each kind is a thin binding returning the shared `ScheduleController`
 * shape that `ScheduleCard` consumes. One schedule-management implementation, two
 * ~20-line bindings — no parallel card components that drift apart.
 */

import type {
  ScheduleResponse,
  ScheduleToggleRequest,
  ScheduleUpsertRequest,
} from "#/api/generated/model";
import {
  useDeleteSyncScheduleApiV1SyncSchedulesTargetIdDelete,
  useGetSyncScheduleApiV1SyncSchedulesTargetIdGet,
  useToggleSyncScheduleApiV1SyncSchedulesTargetIdPatch,
  useUpsertSyncScheduleApiV1SyncSchedulesTargetIdPut,
} from "#/api/generated/schedules/schedules";
import {
  useDeleteWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleDelete,
  useGetWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleGet,
  useToggleWorkflowScheduleApiV1WorkflowsWorkflowIdSchedulePatch,
  useUpsertWorkflowScheduleApiV1WorkflowsWorkflowIdSchedulePut,
} from "#/api/generated/workflows/workflows";
import { STALE } from "#/api/query-client";
import { toasts } from "#/lib/toasts";

export interface ScheduleController {
  schedule: ScheduleResponse | null;
  isLoading: boolean;
  isPending: boolean;
  onSave: (req: ScheduleUpsertRequest) => void;
  onToggle: (enabled: boolean) => void;
  onRemove: () => void;
}

/** Mutation options shared by every schedule write. */
function mutationOpts(errorLabel: string, successMsg?: string) {
  return {
    mutation: {
      onSuccess: () => {
        if (successMsg) toasts.success(successMsg);
      },
      meta: { errorLabel },
    },
  };
}

export function useWorkflowScheduleController(
  workflowId: string,
): ScheduleController {
  // 404 is the expected "no schedule" state — don't retry it.
  const { data, isLoading } =
    useGetWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleGet(workflowId, {
      query: { staleTime: STALE.SLOW, retry: false },
    });
  const schedule = data?.status === 200 ? data.data : null;

  const upsert = useUpsertWorkflowScheduleApiV1WorkflowsWorkflowIdSchedulePut(
    mutationOpts("Failed to save schedule", "Schedule saved"),
  );
  const toggle = useToggleWorkflowScheduleApiV1WorkflowsWorkflowIdSchedulePatch(
    mutationOpts("Failed to update schedule"),
  );
  const remove =
    useDeleteWorkflowScheduleApiV1WorkflowsWorkflowIdScheduleDelete(
      mutationOpts("Failed to remove schedule", "Schedule removed"),
    );

  return {
    schedule,
    isLoading,
    isPending: upsert.isPending || toggle.isPending || remove.isPending,
    onSave: (req) => upsert.mutate({ workflowId, data: req }),
    onToggle: (enabled) =>
      toggle.mutate({
        workflowId,
        data: { enabled } satisfies ScheduleToggleRequest,
      }),
    onRemove: () => remove.mutate({ workflowId }),
  };
}

export function useSyncScheduleController(
  targetId: string,
): ScheduleController {
  const { data, isLoading } = useGetSyncScheduleApiV1SyncSchedulesTargetIdGet(
    targetId,
    { query: { staleTime: STALE.SLOW, retry: false } },
  );
  const schedule = data?.status === 200 ? data.data : null;

  const upsert = useUpsertSyncScheduleApiV1SyncSchedulesTargetIdPut(
    mutationOpts("Failed to save schedule", "Schedule saved"),
  );
  const toggle = useToggleSyncScheduleApiV1SyncSchedulesTargetIdPatch(
    mutationOpts("Failed to update schedule"),
  );
  const remove = useDeleteSyncScheduleApiV1SyncSchedulesTargetIdDelete(
    mutationOpts("Failed to remove schedule", "Schedule removed"),
  );

  return {
    schedule,
    isLoading,
    isPending: upsert.isPending || toggle.isPending || remove.isPending,
    onSave: (req) => upsert.mutate({ targetId, data: req }),
    onToggle: (enabled) =>
      toggle.mutate({
        targetId,
        data: { enabled } satisfies ScheduleToggleRequest,
      }),
    onRemove: () => remove.mutate({ targetId }),
  };
}
