/** Shared SSE event types used across workflow execution, preview, and progress hooks. */

export type NodeExecutionStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed";

export interface NodeStatus {
  nodeId: string;
  nodeType: string;
  status: NodeExecutionStatus;
  executionOrder: number;
  totalNodes: number;
  durationMs?: number;
  inputTrackCount?: number;
  outputTrackCount?: number;
  errorMessage?: string;
}
