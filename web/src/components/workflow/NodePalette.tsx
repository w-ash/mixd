/**
 * Draggable palette of node types for the workflow editor.
 *
 * Fetches available node types from the API, groups by category,
 * and makes each item draggable for drop onto the editor canvas.
 */

import type { DragEvent } from "react";
import { useMemo, useState } from "react";

import { useListNodeTypesApiV1WorkflowsNodesGet } from "#/api/generated/workflows/workflows";
import { Input } from "#/components/ui/input";
import { formatNodeTypeName, NODE_CONFIG } from "#/lib/workflow-config";

interface PaletteItemProps {
  type: string;
  category: string;
  description: string;
}

function PaletteItem({ type, category, description }: PaletteItemProps) {
  const config = NODE_CONFIG[category] ?? NODE_CONFIG.source;
  const Icon = config.Icon;

  const onDragStart = (event: DragEvent) => {
    event.dataTransfer.setData("application/reactflow", type);
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: draggable DnD palette item — interactive via native drag API
    <div
      draggable
      onDragStart={onDragStart}
      className="group flex cursor-grab items-center gap-2.5 rounded-md border-l-2 bg-surface-elevated px-2.5 py-2 transition-colors hover:bg-surface-elevated/80 active:cursor-grabbing"
      style={{ borderLeftColor: config.accentColor }}
      title={description}
    >
      <div
        className="flex size-6 shrink-0 items-center justify-center rounded"
        style={{
          backgroundColor: `color-mix(in oklch, ${config.accentColor} 20%, transparent)`,
        }}
      >
        <Icon
          strokeWidth={1.5}
          className="size-3.5"
          style={{ color: config.accentColor }}
          aria-hidden="true"
        />
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate font-display text-xs text-text">
          {formatNodeTypeName(type)}
        </p>
        <p className="truncate font-body text-[10px] text-text-faint">
          {description}
        </p>
      </div>
    </div>
  );
}

const CATEGORY_ORDER = [
  "source",
  "enricher",
  "filter",
  "sorter",
  "selector",
  "combiner",
  "destination",
];

export function NodePalette() {
  const [search, setSearch] = useState("");
  const { data: nodesData } = useListNodeTypesApiV1WorkflowsNodesGet();
  const nodes = nodesData?.status === 200 ? nodesData.data : [];

  const grouped = useMemo(() => {
    const filtered = search
      ? nodes.filter(
          (n) =>
            n.type.toLowerCase().includes(search.toLowerCase()) ||
            n.description.toLowerCase().includes(search.toLowerCase()),
        )
      : nodes;

    const groups = new Map<string, typeof filtered>();
    for (const node of filtered) {
      const list = groups.get(node.category) ?? [];
      list.push(node);
      groups.set(node.category, list);
    }
    return groups;
  }, [nodes, search]);

  return (
    <div className="flex w-52 flex-col border-r border-border bg-surface-sunken">
      <div className="border-b border-border p-3">
        <Input
          placeholder="Search nodes..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="h-7 text-xs"
        />
      </div>
      <div className="flex-1 overflow-y-auto p-2">
        {CATEGORY_ORDER.map((category) => {
          const items = grouped.get(category);
          if (!items?.length) return null;
          const config = NODE_CONFIG[category];
          return (
            <div key={category} className="mb-3">
              <p
                className="mb-1.5 px-1 font-display text-[10px] font-semibold uppercase tracking-wider"
                style={{ color: config?.accentColor }}
              >
                {config?.label ?? category}
              </p>
              <div className="space-y-1">
                {items.map((node) => (
                  <PaletteItem
                    key={node.type}
                    type={node.type}
                    category={node.category}
                    description={node.description}
                  />
                ))}
              </div>
            </div>
          );
        })}
        {grouped.size === 0 && (
          <p className="px-1 pt-4 text-center font-body text-xs text-text-faint">
            No matching nodes
          </p>
        )}
      </div>
    </div>
  );
}
