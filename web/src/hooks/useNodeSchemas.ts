/**
 * Node type schemas from `GET /workflows/nodes`, with lookup helpers for field
 * labels, option labels, categories and handle visibility.
 *
 * Cached with staleTime: Infinity — node types don't change at runtime. The
 * lookup is derived once per API payload and shared by every caller through a
 * WeakMap, so mounting N nodes costs one Map build and one editor-store write.
 *
 * The store registration exists because the editor's structural edits (edge
 * removal, task rename) consult field declarations.
 */

import { useEffect, useMemo } from "react";

import type {
  ConfigFieldSchema,
  NodeType,
  NodeTypeInfoSchema,
} from "#/api/generated/model";
import { useListNodeTypesApiV1WorkflowsNodesGet } from "#/api/generated/workflows/workflows";
import { fallbackNodeHandles, type NodeHandles } from "#/lib/workflow-config";
import { useEditorStore } from "#/stores/editor-store";

interface NodeEntry {
  fields: ConfigFieldSchema[];
  configLabels: Record<string, string>;
  description: string;
  category: NodeType;
  handles: NodeHandles;
}

export interface NodeSchemas {
  /** Config field schemas for a node type; empty when the type is unknown. */
  getSchema(nodeType: string): ConfigFieldSchema[];
  /** Config key → human-readable label map for a node type. */
  getConfigLabels(nodeType: string): Record<string, string> | undefined;
  /** Human-readable label for a config key on a node type. */
  getFieldLabel(nodeType: string, key: string): string;
  /** Human-readable label for a select option value. */
  getOptionLabel(nodeType: string, key: string, value: string): string;
  /** Node type description. */
  getNodeDescription(nodeType: string): string;
  /** Declared category, or null when no schema is loaded for the type. */
  getCategory(nodeType: string): NodeType | null;
  /** Handle visibility, falling back to the node-type prefix while the
   *  schema for the type is absent. */
  getHandles(nodeType: string): NodeHandles;
  /** Whether the API data is still loading. */
  isLoading: boolean;
}

const NO_FIELDS: ConfigFieldSchema[] = [];

/**
 * Build the per-type lookup.
 *
 * Handles come straight from the IO contract: a node accepts an incoming edge
 * only if it declares an `input_type`, and offers an outgoing edge only if some
 * registered node consumes what it produces. Sources declare no input and
 * destinations produce a terminal type, so each loses one handle.
 */
function buildLookup(nodeTypes: NodeTypeInfoSchema[]): Map<string, NodeEntry> {
  const consumed = new Set(
    nodeTypes.map((nt) => nt.input_type).filter((t) => t != null),
  );

  return new Map(
    nodeTypes.map((nt) => {
      const fields = nt.config_fields ?? NO_FIELDS;
      return [
        nt.type,
        {
          fields,
          configLabels: Object.fromEntries(fields.map((f) => [f.key, f.label])),
          description: nt.description,
          category: nt.category,
          handles: {
            input: nt.input_type != null,
            output: nt.output_type != null && consumed.has(nt.output_type),
          },
        },
      ];
    }),
  );
}

const EMPTY_LOOKUP = new Map<string, NodeEntry>();
const lookupCache = new WeakMap<NodeTypeInfoSchema[], Map<string, NodeEntry>>();

function lookupFor(nodeTypes: NodeTypeInfoSchema[] | undefined) {
  if (!nodeTypes) return EMPTY_LOOKUP;
  let lookup = lookupCache.get(nodeTypes);
  if (!lookup) {
    lookup = buildLookup(nodeTypes);
    lookupCache.set(nodeTypes, lookup);
  }
  return lookup;
}

/** Field-schema map in the shape the editor store keeps. */
const schemaMapCache = new WeakMap<
  Map<string, NodeEntry>,
  Map<string, ConfigFieldSchema[]>
>();

function schemaMapFor(lookup: Map<string, NodeEntry>) {
  let schemas = schemaMapCache.get(lookup);
  if (!schemas) {
    schemas = new Map([...lookup].map(([type, e]) => [type, e.fields]));
    schemaMapCache.set(lookup, schemas);
  }
  return schemas;
}

function makeAccessors(lookup: Map<string, NodeEntry>) {
  const field = (nodeType: string, key: string) =>
    lookup.get(nodeType)?.fields.find((f) => f.key === key);

  return {
    getSchema: (nodeType: string) => lookup.get(nodeType)?.fields ?? NO_FIELDS,
    getConfigLabels: (nodeType: string) => lookup.get(nodeType)?.configLabels,
    getFieldLabel: (nodeType: string, key: string) =>
      field(nodeType, key)?.label ?? key,
    getOptionLabel: (nodeType: string, key: string, value: string) =>
      field(nodeType, key)?.options?.find((o) => o.value === value)?.label ??
      value,
    getNodeDescription: (nodeType: string) =>
      lookup.get(nodeType)?.description ?? "",
    getCategory: (nodeType: string) => lookup.get(nodeType)?.category ?? null,
    getHandles: (nodeType: string) =>
      lookup.get(nodeType)?.handles ?? fallbackNodeHandles(nodeType),
  };
}

const accessorCache = new WeakMap<
  Map<string, NodeEntry>,
  ReturnType<typeof makeAccessors>
>();

function accessorsFor(lookup: Map<string, NodeEntry>) {
  let accessors = accessorCache.get(lookup);
  if (!accessors) {
    accessors = makeAccessors(lookup);
    accessorCache.set(lookup, accessors);
  }
  return accessors;
}

export function useNodeSchemas(): NodeSchemas {
  const { data, isLoading } = useListNodeTypesApiV1WorkflowsNodesGet({
    query: { staleTime: Number.POSITIVE_INFINITY },
  });

  const lookup = lookupFor(data?.status === 200 ? data.data : undefined);

  useEffect(() => {
    if (lookup === EMPTY_LOOKUP) return;
    const schemas = schemaMapFor(lookup);
    const store = useEditorStore.getState();
    if (store.nodeSchemas !== schemas) store.setNodeSchemas(schemas);
  }, [lookup]);

  return useMemo(
    () => ({ ...accessorsFor(lookup), isLoading }),
    [lookup, isLoading],
  );
}
