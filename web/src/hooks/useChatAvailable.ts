import { useGetAssistantStatusApiV1AssistantStatusGet } from "#/api/generated/assistant/assistant";
import type { AssistantStatusResponseSource } from "#/api/generated/model/assistantStatusResponseSource";

interface ChatAvailability {
  /** True once we know the current user has a usable Anthropic credential. */
  available: boolean;
  /** Which credential serves this user ("user" | "server"), or null. */
  source: AssistantStatusResponseSource | null;
  /** True while the status is still resolving (gate should render nothing). */
  isLoading: boolean;
  /** True when the status query failed. Gate consumers ignore it (fail closed);
   *  the settings page surfaces an explicit error instead of the connect form. */
  isError: boolean;
  /** The status query error, when {@link isError}. */
  error: unknown;
}

/**
 * Per-user gate for the entire assistant surface (v0.9.0.1 BYO-key).
 *
 * The chat panel, edge tab, Cmd+K binding, and mobile "Ask" entry all consume
 * this and render **nothing** until the current user has connected their own
 * Anthropic key (or a server fallback is configured). Absence is intentional —
 * we never show an affordance that only errors when used.
 *
 * Fails closed: while loading, or on any error, `available` is false.
 */
export function useChatAvailable(): ChatAvailability {
  const { data, isLoading, isError, error } =
    useGetAssistantStatusApiV1AssistantStatusGet({
      query: {
        // Availability rarely changes mid-session; refetch on focus so a key
        // connected in another tab lights up the surface without a reload.
        staleTime: 60_000,
      },
    });

  const status = data?.status === 200 ? data.data : undefined;
  return {
    available: status?.connected ?? false,
    source: status?.source ?? null,
    isLoading,
    isError,
    error,
  };
}
