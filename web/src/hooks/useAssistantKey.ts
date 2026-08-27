import {
  useDeleteAssistantKeyApiV1AssistantKeyDelete,
  useProbeAssistantKeyApiV1AssistantKeyTestPost,
  usePutAssistantKeyApiV1AssistantKeyPut,
} from "#/api/generated/assistant/assistant";
import { toasts } from "#/lib/toasts";

/**
 * Connect / test / remove the current user's Anthropic API key (v0.9.0.1).
 *
 * The key is write-only: `connect` validates and stores it (never echoed back),
 * `remove` deletes it, and both invalidate the availability query so the whole
 * chat surface appears or disappears immediately. Validation errors
 * (`INVALID_API_KEY`) surface on `connectError` for inline display rather than a
 * toast — the settings form is the place to fix them.
 */
export function useAssistantKey() {
  const connectMutation = usePutAssistantKeyApiV1AssistantKeyPut({
    mutation: {
      onSuccess: () => toasts.success("AI assistant connected"),
      // Inline error on the form; suppress the global error toast.
      meta: { suppressErrorToast: true },
    },
  });

  const removeMutation = useDeleteAssistantKeyApiV1AssistantKeyDelete({
    mutation: {
      onSuccess: () => toasts.success("AI assistant disconnected"),
      meta: { errorLabel: "Failed to remove the API key" },
    },
  });

  const testMutation = useProbeAssistantKeyApiV1AssistantKeyTestPost({
    mutation: { meta: { errorLabel: "Failed to test the API key" } },
  });

  return {
    connect: (apiKey: string) =>
      connectMutation.mutateAsync({ data: { api_key: apiKey } }),
    isConnecting: connectMutation.isPending,
    connectError: connectMutation.error,
    remove: () => removeMutation.mutate(undefined),
    isRemoving: removeMutation.isPending,
    test: () => testMutation.mutateAsync({ data: {} }),
    isTesting: testMutation.isPending,
  };
}
