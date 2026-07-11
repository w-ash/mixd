import { Check, Loader2 } from "lucide-react";

import type { ToolCall } from "#/stores/chat-store";

// Friendly labels for the assistant's tools. Phase 1 wires the real backend
// tool names; anything not listed falls back to the humanized tool id, so a
// new tool still reads sensibly before it earns a bespoke label.
const TOOL_LABELS: Record<string, string> = {
  search_tracks: "tracks",
  get_liked_tracks: "liked tracks",
  get_listening_history: "listening history",
  get_top_tracks: "top tracks",
  get_playlists: "playlists",
  get_playlist: "playlist",
  get_starred_tracks: "starred tracks",
  create_playlist: "playlist",
  update_playlist: "playlist update",
  add_tracks_to_playlist: "playlist tracks",
  build_workflow: "workflow",
  run_workflow: "workflow run",
  delegate_analysis: "deep analysis",
};

function humanize(name: string): string {
  return name.replace(/^(get|search|list)_/, "").replaceAll("_", " ");
}

export function ToolCallIndicator({ toolCall }: { toolCall: ToolCall }) {
  const label = TOOL_LABELS[toolCall.name] ?? humanize(toolCall.name);
  const isDone = toolCall.result !== undefined;

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border px-2.5 py-0.5 font-display text-xs text-text-muted">
      {isDone ? (
        <Check className="size-3" />
      ) : (
        <Loader2 className="size-3 animate-spin" />
      )}
      {isDone
        ? `Checked ${label}`
        : toolCall.kind === "write"
          ? `Proposing ${label}…`
          : `Looking up ${label}…`}
    </span>
  );
}
