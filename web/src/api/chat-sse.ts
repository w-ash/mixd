/**
 * SSE client for the chat endpoint.
 *
 * Shares the `connectToSSE` transport with the operations stream — chat only
 * differs in being POST-bodied and ephemeral (no `Last-Event-ID` reconnect).
 * Frames carry no `event:` name, only a `type`-tagged JSON payload.
 */

import { connectToSSE, SSEHttpError } from "#/api/sse-client";
import type { ToolKind } from "#/stores/chat-store";

/**
 * Frames the chat stream emits, mirroring `src/interface/api/chat_sse.py`.
 *
 * Declared here rather than generated: the endpoint streams bespoke JSON lines
 * instead of a response model, so these shapes are not in `openapi.json`.
 */
type ChatStreamEvent =
  | { type: "token"; text: string }
  | { type: "tool_start"; name: string; id: string; kind: string }
  | {
      type: "tool_result";
      name: string;
      id: string;
      summary: unknown;
      is_error: boolean;
    }
  | { type: "code_start"; id: string; command: string }
  | {
      type: "code_result";
      id: string;
      stdout: string;
      stderr: string;
      return_code: number;
    }
  | { type: "done" }
  | { type: "error"; code: string; message: string };

/** Local calendar date (YYYY-MM-DD) so "this month" resolves to the user's clock. */
function localISODate(): string {
  const d = new Date();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${month}-${day}`;
}

export interface ChatSSECallbacks {
  onToken: (text: string) => void;
  onToolStart: (name: string, id: string, kind: ToolKind) => void;
  onToolResult: (
    name: string,
    id: string,
    summary: unknown,
    isError: boolean,
  ) => void;
  onCodeStart: (id: string, command: string) => void;
  onCodeResult: (
    id: string,
    stdout: string,
    stderr: string,
    returnCode: number,
  ) => void;
  onDone: () => void;
  onError: (code: string, message: string) => void;
}

/** Decode one frame. Null for the `[DONE]` sentinel or anything unparseable. */
function parseChatEvent(data: string): ChatStreamEvent | null {
  if (!data || data === "[DONE]") return null;
  try {
    const parsed: unknown = JSON.parse(data);
    return parsed !== null &&
      typeof parsed === "object" &&
      typeof (parsed as { type?: unknown }).type === "string"
      ? (parsed as ChatStreamEvent)
      : null;
  } catch {
    return null;
  }
}

function dispatch(event: ChatStreamEvent, callbacks: ChatSSECallbacks): void {
  switch (event.type) {
    case "token":
      callbacks.onToken(event.text);
      break;
    case "tool_start":
      callbacks.onToolStart(
        event.name,
        event.id,
        event.kind === "write" || event.kind === "agentic"
          ? event.kind
          : "read",
      );
      break;
    case "tool_result":
      callbacks.onToolResult(
        event.name,
        event.id,
        event.summary,
        event.is_error ?? false,
      );
      break;
    case "code_start":
      callbacks.onCodeStart(event.id, event.command);
      break;
    case "code_result":
      callbacks.onCodeResult(
        event.id,
        event.stdout,
        event.stderr,
        event.return_code,
      );
      break;
    case "done":
      callbacks.onDone();
      break;
    case "error":
      callbacks.onError(event.code, event.message);
      break;
  }
}

/** Read the API's JSON error envelope from a rejected handshake. */
async function errorEnvelope(
  response: Response,
  status: number,
): Promise<{ code: string; message: string }> {
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string };
    };
    return {
      code: body.error?.code ?? "REQUEST_FAILED",
      message: body.error?.message ?? `HTTP ${status}`,
    };
  } catch {
    return { code: "REQUEST_FAILED", message: `HTTP ${status}` };
  }
}

export interface ConfirmationPayload {
  action_id: string;
  approved: boolean;
}

export async function sendChatMessage(
  messages: { role: "user" | "assistant"; content: string }[],
  callbacks: ChatSSECallbacks,
  signal: AbortSignal,
  confirmation?: ConfirmationPayload,
  effort?: string,
  currentWorkflowId?: string,
  page?: string,
): Promise<void> {
  const body: Record<string, unknown> = {
    messages,
    client_date: localISODate(),
  };
  if (confirmation) body.confirmation = confirmation;
  if (effort) body.effort = effort;
  if (currentWorkflowId !== undefined) {
    body.current_workflow_id = currentWorkflowId;
  }
  // The coarse UI section the user is on, for server-side tool routing.
  if (page !== undefined) body.page = page;

  try {
    const events = await connectToSSE("/api/v1/chat", signal, {
      method: "POST",
      body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" },
      // No handshake budget: the turn runs its confirmed tool calls before the
      // first byte, so a long one is work in progress, not a dead connection.
      // The user's Stop button is the way out of a turn that runs too long.
      connectionTimeoutMs: 0,
    });

    let completed = false;
    for await (const frame of events) {
      const event = parseChatEvent(frame.data);
      if (!event) continue;
      if (event.type === "done" || event.type === "error") completed = true;
      dispatch(event, callbacks);
    }

    if (!completed && !signal.aborted) {
      callbacks.onError(
        "STREAM_ENDED",
        "Response stream ended unexpectedly. Please try again.",
      );
    }
  } catch (error) {
    // A user-initiated abort is not an error — the store already finalized the
    // message on stop, so stay silent rather than flashing a failure.
    if (signal.aborted) return;
    if (error instanceof SSEHttpError) {
      const { code, message } = await errorEnvelope(
        error.response,
        error.status,
      );
      callbacks.onError(code, message);
      return;
    }
    callbacks.onError(
      "NETWORK_ERROR",
      error instanceof Error ? error.message : "Network request failed",
    );
  }
}
