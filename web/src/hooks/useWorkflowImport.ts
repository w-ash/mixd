import { useCallback, useRef } from "react";
import { useNavigate } from "react-router";

import { EDITOR_SEED_STATE } from "#/lib/editor-entry";
import { toasts } from "#/lib/toasts";
import { loadImportedWorkflowDef } from "#/lib/workflow-file";

/**
 * Shared workflow-file import for the editor toolbar and the list page.
 *
 * Both entry points do the same thing: read the picked file, seed the editor
 * store with the parsed draft, then open the editor on it as an unsaved
 * workflow. Opening via `/workflows/new` (rather than staying put) detaches the
 * canvas from any existing-workflow route query — so importing while editing a
 * saved workflow can't be clobbered by a background refetch re-running the
 * editor's load effect. The typed `EDITOR_SEED_STATE` location state declares a
 * `seed` entry so the editor adopts the store it was just handed instead of
 * resetting it (see `lib/editor-entry.ts`).
 *
 * Returns a click handler for the visible button and props to spread onto a
 * hidden file <input>; callers own only the button's appearance.
 */
export function useWorkflowImport() {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

  const open = useCallback(() => inputRef.current?.click(), []);

  const onChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = ""; // allow re-importing the same filename
      if (!file) return;
      try {
        loadImportedWorkflowDef(await file.text());
        navigate("/workflows/new", { state: EDITOR_SEED_STATE });
        toasts.success("Workflow imported", {
          description: "Review the canvas, then Save to keep it.",
        });
      } catch (err) {
        toasts.error("Couldn't import workflow", err);
      }
    },
    [navigate],
  );

  return {
    open,
    inputRef,
    inputProps: {
      type: "file" as const,
      accept: ".json,application/json",
      onChange,
      className: "hidden",
      "aria-label": "Import workflow file",
    },
  };
}
