/**
 * Right sidebar panel for editing a selected node's configuration.
 *
 * Reads the selected node from the editor store, looks up its config schema,
 * and renders a dynamic form with two-way binding via store.updateNodeConfig().
 */

import { X } from "lucide-react";
import { NodeTypeBadge } from "@/components/shared/NodeTypeBadge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  type ConfigFieldSchema,
  getNodeConfigSchema,
} from "@/lib/node-config-schema";
import { formatNodeTypeName, getNodeCategory } from "@/lib/workflow-config";
import { useEditorStore } from "@/stores/editor-store";

function ConfigField({
  field,
  value,
  onChange,
}: {
  field: ConfigFieldSchema;
  value: unknown;
  onChange: (key: string, value: unknown) => void;
}) {
  switch (field.type) {
    case "text":
      return (
        <Input
          className="h-8 font-mono text-xs"
          placeholder={field.placeholder}
          value={(value as string) ?? ""}
          onChange={(e) => onChange(field.key, e.target.value || undefined)}
        />
      );

    case "number":
      return (
        <Input
          type="number"
          className="h-8 font-mono text-xs"
          placeholder={field.placeholder}
          min={field.min}
          max={field.max}
          value={value != null ? String(value) : ""}
          onChange={(e) => {
            const num = e.target.value ? Number(e.target.value) : undefined;
            onChange(field.key, num);
          }}
        />
      );

    case "select":
      return (
        <Select
          value={(value as string) ?? (field.defaultValue as string) ?? ""}
          onValueChange={(v) => onChange(field.key, v)}
        >
          <SelectTrigger className="h-8 w-full font-display text-xs">
            <SelectValue placeholder="Select..." />
          </SelectTrigger>
          <SelectContent>
            {field.options?.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );

    case "boolean": {
      const checked =
        value != null ? Boolean(value) : Boolean(field.defaultValue);
      return (
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          onClick={() => onChange(field.key, !checked)}
          className="group relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border border-border bg-surface-sunken transition-colors aria-checked:border-primary aria-checked:bg-primary/30"
        >
          <span
            className={`pointer-events-none block size-4 rounded-full shadow-sm transition-transform ${checked ? "translate-x-4 bg-primary" : "translate-x-0 bg-text-muted"}`}
          />
        </button>
      );
    }
  }
}

export function NodeConfigPanel() {
  const node = useEditorStore((s) =>
    s.nodes.find((n) => n.id === s.selectedNodeId),
  );
  const allNodes = useEditorStore((s) => s.nodes);
  const updateNodeConfig = useEditorStore((s) => s.updateNodeConfig);
  const updateNodeTaskId = useEditorStore((s) => s.updateNodeTaskId);
  const selectNode = useEditorStore((s) => s.selectNode);

  if (!node) return null;

  const nodeType = node.data.nodeType as string;
  const config = node.data.config as Record<string, unknown>;
  const taskId = node.data.taskId as string;
  const categoryConfig = getNodeCategory(nodeType);
  const schema = getNodeConfigSchema(nodeType);

  const handleFieldChange = (key: string, value: unknown) => {
    const next = { ...config };
    if (value === undefined || value === "") {
      delete next[key];
    } else {
      next[key] = value;
    }
    updateNodeConfig(node.id, next);
  };

  const handleTaskIdChange = (newId: string) => {
    const sanitized = newId.replace(/[^a-z0-9_]/g, "");
    if (sanitized && sanitized !== node.id) {
      const isDuplicate = allNodes.some(
        (n) => n.id === sanitized && n.id !== node.id,
      );
      if (!isDuplicate) {
        updateNodeTaskId(node.id, sanitized);
      }
    }
  };

  return (
    <div className="flex w-72 flex-col border-l border-border bg-surface-sunken">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div className="flex items-center gap-2">
          <categoryConfig.Icon
            size={14}
            strokeWidth={1.5}
            style={{ color: categoryConfig.accentColor }}
            aria-hidden="true"
          />
          <NodeTypeBadge nodeType={nodeType} />
        </div>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => selectNode(null)}
          aria-label="Close panel"
        >
          <X size={14} />
        </Button>
      </div>

      {/* Form */}
      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        {/* Node label */}
        <p className="font-display text-xs font-medium text-text">
          {formatNodeTypeName(nodeType)}
        </p>

        {/* Task ID */}
        <div className="space-y-1.5">
          <p className="font-display text-[11px] font-medium uppercase tracking-wider text-text-muted">
            Task ID
          </p>
          <Input
            className="h-8 font-mono text-xs"
            value={taskId}
            onChange={(e) => handleTaskIdChange(e.target.value)}
            aria-label="Task ID"
          />
        </div>

        {/* Dynamic config fields */}
        {schema.length > 0 && (
          <div
            className="h-px w-full"
            style={{
              backgroundColor: `color-mix(in oklch, ${categoryConfig.accentColor} 20%, transparent)`,
            }}
          />
        )}

        {schema.map((field) => (
          <div key={field.key} className="space-y-1.5">
            <p className="flex items-baseline gap-1 font-display text-[11px] font-medium uppercase tracking-wider text-text-muted">
              {field.label}
              {field.required && (
                <span className="text-destructive" title="required">
                  *
                </span>
              )}
            </p>
            {field.description && (
              <p className="font-body text-[10px] text-text-faint">
                {field.description}
              </p>
            )}
            <ConfigField
              field={field}
              value={config[field.key]}
              onChange={handleFieldChange}
            />
          </div>
        ))}

        {schema.length === 0 && (
          <p className="pt-2 font-body text-xs text-text-faint">
            This node has no configurable fields.
          </p>
        )}
      </div>
    </div>
  );
}
