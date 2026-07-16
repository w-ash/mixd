/**
 * SSE client for the chat endpoint.
 *
 * Uses fetch + ReadableStream (not EventSource) because the endpoint is POST.
 * Distinct from `api/sse-client.ts` (the GET/EventSource operations-progress
 * transport) — chat is ephemeral and POST-bodied, so it needs the streaming
 * `fetch` reader instead.
 */

import { getAuthToken } from "#/api/auth";
import type { ToolKind } from "#/stores/chat-store";

const TERMINAL_TYPES = new Set(["done", "error"]);

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

function parseSSELine(line: string): Record<string, unknown> | null {
  if (!line.startsWith("data: ")) return null;
  const json = line.slice(6);
  if (json === "[DONE]") return null;
  try {
    return JSON.parse(json) as Record<string, unknown>;
  } catch {
    return null;
  }
}

async function readSSEStream(
  response: Response,
  onEvent: (event: Record<string, unknown>) => void,
  signal: AbortSignal,
): Promise<{ completed: boolean }> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("Response body is not readable");

  const decoder = new TextDecoder();
  let buffer = "";
  let completed = false;

  const flush = (raw: string): void => {
    const event = parseSSELine(raw.trim());
    if (!event) return;
    if (TERMINAL_TYPES.has(event.type as string)) completed = true;
    onEvent(event);
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.trim()) flush(line);
      }
    }
    if (buffer.trim()) flush(buffer);
  } catch (error) {
    if (signal.aborted) return { completed: true };
    throw error;
  }

  return { completed };
}

function handleChatEvents(callbacks: ChatSSECallbacks) {
  return (event: Record<string, unknown>): void => {
    switch (event.type) {
      case "token":
        callbacks.onToken(event.text as string);
        break;
      case "tool_start":
        callbacks.onToolStart(
          event.name as string,
          event.id as string,
          event.kind === "write" || event.kind === "agentic"
            ? event.kind
            : "read",
        );
        break;
      case "tool_result":
        callbacks.onToolResult(
          event.name as string,
          event.id as string,
          event.summary,
          (event.is_error as boolean) ?? false,
        );
        break;
      case "code_start":
        callbacks.onCodeStart(event.id as string, event.command as string);
        break;
      case "code_result":
        callbacks.onCodeResult(
          event.id as string,
          event.stdout as string,
          event.stderr as string,
          event.return_code as number,
        );
        break;
      case "done":
        callbacks.onDone();
        break;
      case "error":
        callbacks.onError(event.code as string, event.message as string);
        break;
    }
  };
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
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    const token = await getAuthToken();
    if (token) headers.Authorization = `Bearer ${token}`;

    const response = await fetch("/api/v1/chat", {
      method: "POST",
      headers,
      body: JSON.stringify(body),
      signal,
    });

    if (!response.ok) {
      let code = "REQUEST_FAILED";
      let message = `HTTP ${response.status}`;
      try {
        const errBody = (await response.json()) as {
          error?: { code?: string; message?: string };
        };
        if (errBody.error) {
          code = errBody.error.code ?? code;
          message = errBody.error.message ?? message;
        }
      } catch {
        // ignore parse errors
      }
      callbacks.onError(code, message);
      return;
    }

    const { completed } = await readSSEStream(
      response,
      handleChatEvents(callbacks),
      signal,
    );
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
    callbacks.onError(
      "NETWORK_ERROR",
      error instanceof Error ? error.message : "Network request failed",
    );
  }
}
