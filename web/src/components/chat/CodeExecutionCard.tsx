import { Check, ChevronRight, Loader2, SquareTerminal } from "lucide-react";
import { useState } from "react";
import { cn } from "#/lib/utils";
import type { CodeExecution } from "#/stores/chat-store";

// Renders one sandbox execution: the assistant streams `code_start`/`code_result`
// frames (v0.9.2) which the store collects into `message.codeExecutions`.

const monoBlockClass =
  "overflow-x-auto rounded-lg bg-surface-sunken p-3 font-mono text-xs leading-normal text-text";

function statusLabel(execution: CodeExecution): string {
  if (execution.returnCode === undefined) return "Running code…";
  if (execution.returnCode !== 0)
    return `Code failed (exit ${execution.returnCode})`;
  return "Ran code";
}

export function CodeExecutionCard({ execution }: { execution: CodeExecution }) {
  const [expanded, setExpanded] = useState(false);
  const isRunning = execution.returnCode === undefined;
  const failed = !isRunning && execution.returnCode !== 0;
  const hasOutput = Boolean(execution.stdout || execution.stderr);

  return (
    <div className="rounded-lg border border-border bg-surface px-4 py-3">
      <div className="flex items-center gap-1.5 font-display text-xs text-text-muted">
        <SquareTerminal className="size-3.5" />
        {isRunning ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <Check className="size-3" />
        )}
        <span className={cn(failed && "text-destructive")}>
          {statusLabel(execution)}
        </span>
      </div>
      <pre className={cn(monoBlockClass, "mt-2 max-h-48")}>
        {execution.command}
      </pre>
      {hasOutput && (
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="mt-2 flex items-center gap-1 rounded-md font-display text-xs text-text-muted transition-colors hover:text-text"
        >
          <ChevronRight
            className={cn(
              "size-3.5 transition-transform duration-150",
              expanded && "rotate-90",
            )}
          />
          {expanded ? "Hide output" : "Show output"}
        </button>
      )}
      {expanded && hasOutput && (
        <div className="mt-2 flex flex-col gap-2">
          {execution.stdout && (
            <pre className={monoBlockClass}>{execution.stdout}</pre>
          )}
          {execution.stderr && (
            <pre className={cn(monoBlockClass, "text-destructive")}>
              {execution.stderr}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
