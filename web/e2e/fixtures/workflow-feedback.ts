/**
 * Fixtures + route control for the workflow save/run feedback E2E spec.
 *
 * **Nothing here reaches a real backend.** The Playwright suite runs against a
 * bare Vite dev server with no API process, and every `/api/v1/` request is
 * fulfilled from the data below — including the run endpoint and its SSE
 * stream. "Running a workflow" in these tests never touches the application
 * layer, a connector, or Spotify.
 *
 * Two controls exist that plain canned responses can't provide, and both are
 * what make "immediately" assertable without `waitForTimeout`:
 *
 *  - {@link Deferred} — a route whose handler parks until the test releases it.
 *    Holding the workflow GET open after a save proves the detail page rendered
 *    from the mutation's cache write rather than from a refetch.
 *  - {@link SSEStream} — an event-stream body assembled and released on demand,
 *    so a run can be observed mid-flight and then driven to completion.
 *
 * Types are imported type-only from the generated model, so a schema change
 * that breaks these fixtures is a `tsc` error (a sibling Vitest file in `src/`
 * pulls them into the type program — `tsconfig` only includes `src/`).
 */
import type {
  WorkflowDetailSchema,
  WorkflowRunSummarySchema,
  WorkflowSummarySchema,
} from "../../src/api/generated/model";

export const WF_A = "019d0000-0000-7000-8000-00000000000a";
export const WF_B = "019d0000-0000-7000-8000-00000000000b";
export const RUN_ID = "019d0000-0000-7000-8000-0000000000f1";
export const OPERATION_ID = "op-e2e-1";

// ─── Deferred route control ──────────────────────────────────────────────────

/** A promise the test resolves when it wants a parked route to respond. */
export class Deferred {
  private resolveFn: (() => void) | null = null;
  readonly promise: Promise<void>;

  constructor() {
    this.promise = new Promise<void>((resolve) => {
      this.resolveFn = resolve;
    });
  }

  release(): void {
    this.resolveFn?.();
  }
}

// ─── SSE stream assembly ─────────────────────────────────────────────────────

export interface SSEEvent {
  event: string;
  data: unknown;
}

/**
 * Serialise events into a `text/event-stream` body.
 *
 * Matches the wire format the app's parser expects: numeric `id:` for
 * `Last-Event-ID` resume, `event:` name, single-line JSON `data:`.
 */
export function sseBody(events: SSEEvent[]): string {
  return events
    .map(
      (e, i) =>
        `id: ${i + 1}\nevent: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`,
    )
    .join("");
}

/** `run_accepted` → one `node_status` per node → `complete`. */
export function runToCompletion(nodeIds: string[]): SSEEvent[] {
  const events: SSEEvent[] = [
    { event: "run_accepted", data: { run_id: RUN_ID } },
  ];
  nodeIds.forEach((nodeId, index) => {
    events.push({
      event: "node_status",
      data: {
        node_id: nodeId,
        node_type: "source.liked_tracks",
        status: "completed",
        execution_order: index + 1,
        total_nodes: nodeIds.length,
        duration_ms: 120,
        output_track_count: 42,
      },
    });
  });
  events.push({ event: "complete", data: { final_status: "completed" } });
  return events;
}

// ─── Workflow payloads ───────────────────────────────────────────────────────

function tasks(count: number) {
  return Array.from({ length: count }, (_, i) => ({
    id: `task_${i + 1}`,
    type: "source.liked_tracks",
    config: {},
    upstream: i === 0 ? [] : [`task_${i}`],
  }));
}

export function workflowDetail(
  overrides: Partial<WorkflowDetailSchema> = {},
): WorkflowDetailSchema {
  const taskCount = overrides.task_count ?? 3;
  return {
    id: WF_A,
    name: "Weekly Obsessions",
    description: "Heavy rotation, refreshed weekly",
    definition_version: 1,
    task_count: taskCount,
    node_types: ["source.liked_tracks"],
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    last_run: null,
    successful_run_count: 0,
    definition: {
      id: "weekly_obsessions",
      name: "Weekly Obsessions",
      description: "Heavy rotation, refreshed weekly",
      version: "1.0",
      tasks: tasks(taskCount),
    },
    ...overrides,
  };
}

export function workflowSummary(
  overrides: Partial<WorkflowSummarySchema> = {},
): WorkflowSummarySchema {
  return {
    id: WF_A,
    name: "Weekly Obsessions",
    description: "Heavy rotation, refreshed weekly",
    definition_version: 1,
    task_count: 3,
    node_types: ["source.liked_tracks"],
    created_at: "2026-05-01T00:00:00Z",
    updated_at: "2026-06-01T00:00:00Z",
    last_run: null,
    successful_run_count: 0,
    ...overrides,
  };
}

export function activeRun(
  overrides: Partial<WorkflowRunSummarySchema> = {},
): WorkflowRunSummarySchema {
  return {
    id: RUN_ID,
    workflow_id: WF_A,
    run_number: 5,
    status: "running",
    definition_version: 1,
    operation_id: OPERATION_ID,
    started_at: "2026-06-01T10:00:00Z",
    ...overrides,
  };
}

/** Paginated envelope shared by every list endpoint. */
export function paginated(data: unknown[]) {
  return { data, total: data.length, limit: 50, offset: 0, next_cursor: null };
}
