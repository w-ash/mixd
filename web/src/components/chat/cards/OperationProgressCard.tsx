import { ExternalLink } from "lucide-react";
import { Link } from "react-router";

import { ChatCard } from "#/components/chat/ChatCard";
import { OperationProgress } from "#/components/shared/OperationProgress";
import { useOperationProgress } from "#/hooks/useOperationProgress";

import type { OperationStartedResult } from "./operation-progress-types";

/**
 * Live progress for a long-running operation the assistant launched (import,
 * playlist sync, workflow run). Subscribes to the operation's SSE stream via
 * the shared {@link useOperationProgress} hook and renders the same
 * {@link OperationProgress} bar the settings pages use, inside a compact
 * chat-card shell that matches WorkflowPreviewCard / ConfirmationCard.
 *
 * Terminal states (complete/failed/cancelled) are modelled by the hook and
 * rendered by OperationProgress — the card just keeps its shell around them.
 * When the backend recorded a persistent run, "Open run" deep-links to the
 * import-history row (`/settings/imports?run=<id>`), the app's run-detail view.
 */
export function OperationProgressCard({
  result,
}: {
  result: OperationStartedResult;
}) {
  const { progress } = useOperationProgress(result.operation_id);

  return (
    <ChatCard
      className="w-full"
      header={
        <div className="mb-2 flex items-baseline justify-between gap-2">
          <p className="font-display text-xs font-medium text-text">
            {result.description}
          </p>
          {result.run_id && (
            <Link
              to={`/settings/imports?run=${result.run_id}`}
              className="inline-flex shrink-0 items-center gap-1 font-display text-xs text-primary underline-offset-2 hover:underline"
            >
              <ExternalLink className="size-3" />
              Open run
            </Link>
          )}
        </div>
      }
    >
      {progress ? (
        <OperationProgress progress={progress} />
      ) : (
        <p className="font-body text-xs text-text-muted">Connecting…</p>
      )}
    </ChatCard>
  );
}
