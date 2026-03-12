/**
 * Hook that fetches node type schemas from the API and provides
 * lookup helpers for field labels, option labels, and field definitions.
 *
 * Replaces the static node-config-schema.ts with API-driven data.
 * Cached with staleTime: Infinity — node types don't change at runtime.
 */

import { useMemo } from "react";

import type { ConfigFieldSchema } from "@/api/generated/model";
import { useListNodeTypesApiV1WorkflowsNodesGet } from "@/api/generated/workflows/workflows";

export interface NodeSchemas {
  /** Get all config field schemas for a node type. */
  getSchema(nodeType: string): ConfigFieldSchema[];
  /** Get the human-readable label for a config key on a node type. */
  getFieldLabel(nodeType: string, key: string): string;
  /** Get the human-readable label for a select option value. */
  getOptionLabel(nodeType: string, key: string, value: string): string;
  /** Get the node type description. */
  getNodeDescription(nodeType: string): string;
  /** Whether the API data is still loading. */
  isLoading: boolean;
}

export function useNodeSchemas(): NodeSchemas {
  const { data, isLoading } = useListNodeTypesApiV1WorkflowsNodesGet({
    query: { staleTime: Number.POSITIVE_INFINITY },
  });

  const nodeTypes = data?.status === 200 ? data.data : undefined;

  const lookup = useMemo(() => {
    if (!nodeTypes) {
      return {
        schemas: new Map<string, ConfigFieldSchema[]>(),
        descriptions: new Map<string, string>(),
      };
    }

    const schemas = new Map<string, ConfigFieldSchema[]>();
    const descriptions = new Map<string, string>();
    for (const nt of nodeTypes) {
      schemas.set(nt.type, nt.config_fields ?? []);
      descriptions.set(nt.type, nt.description);
    }

    return { schemas, descriptions };
  }, [nodeTypes]);

  return useMemo(
    () => ({
      getSchema(nodeType: string): ConfigFieldSchema[] {
        return lookup.schemas.get(nodeType) ?? [];
      },

      getFieldLabel(nodeType: string, key: string): string {
        const fields = lookup.schemas.get(nodeType);
        if (!fields) return key;
        const field = fields.find((f) => f.key === key);
        return field?.label ?? key;
      },

      getOptionLabel(nodeType: string, key: string, value: string): string {
        const fields = lookup.schemas.get(nodeType);
        if (!fields) return value;
        const field = fields.find((f) => f.key === key);
        if (!field?.options) return value;
        const option = field.options.find((o) => o.value === value);
        return option?.label ?? value;
      },

      getNodeDescription(nodeType: string): string {
        return lookup.descriptions.get(nodeType) ?? "";
      },

      isLoading,
    }),
    [lookup, isLoading],
  );
}
