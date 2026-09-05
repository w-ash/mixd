import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { createElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it } from "vitest";

import type { NodeTypeInfoSchema } from "#/api/generated/model";
import { useEditorStore } from "#/stores/editor-store";
import { server } from "#/test/setup";

import { useNodeSchemas } from "./useNodeSchemas";

// ─── Wrapper ────────────────────────────────────────────────────

function createWrapper() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
    },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return createElement(QueryClientProvider, { client }, children);
  };
}

// ─── Test fixtures ──────────────────────────────────────────────

const TEST_NODE_TYPES: NodeTypeInfoSchema[] = [
  {
    type: "filter.play_count",
    category: "filter",
    description: "Filter tracks by play count",
    config_fields: [
      {
        key: "min_plays",
        label: "Minimum Plays",
        field_type: "number",
        required: true,
        min: 0,
        max: 10000,
        options: [],
      },
      {
        key: "period",
        label: "Time Period",
        field_type: "select",
        required: false,
        options: [
          { value: "7d", label: "Last 7 Days" },
          { value: "30d", label: "Last 30 Days" },
          { value: "all", label: "All Time" },
        ],
      },
    ],
  },
  {
    type: "source.liked_tracks",
    category: "source",
    description: "Fetch liked tracks from a connector",
    config_fields: [],
  },
  {
    type: "enricher.play_history",
    category: "enricher",
    description: "Attach play-history metrics",
    config_fields: [
      {
        key: "metrics",
        label: "Metrics",
        field_type: "multi_select",
        required: false,
        default: ["total_plays", "last_played_dates"],
        options: [
          { value: "total_plays", label: "Total Plays" },
          { value: "last_played_dates", label: "Last Played" },
          { value: "period_plays", label: "Period Plays" },
        ],
      },
      {
        key: "primary_input",
        label: "Primary Input",
        field_type: "task_ref",
        required: false,
        options: [],
      },
    ],
  },
];

// ─── Tests ──────────────────────────────────────────────────────

describe("useNodeSchemas", () => {
  beforeEach(() => {
    server.use(
      http.get("*/api/v1/workflows/nodes", () => {
        return HttpResponse.json(TEST_NODE_TYPES, { status: 200 });
      }),
    );
  });

  it("isLoading is true initially", () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);
  });

  it("registers the fetched schemas with the editor store", async () => {
    useEditorStore.setState({ nodeSchemas: new Map() });
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    const registered = useEditorStore.getState().nodeSchemas;
    expect(registered.get("filter.play_count")).toBe(
      result.current.getSchema("filter.play_count"),
    );
    expect(registered.get("source.liked_tracks")).toEqual([]);
  });

  it("returns empty schema for unknown node types", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.getSchema("nonexistent.type")).toEqual([]);
  });

  it("returns field schemas for known node types", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    const fields = result.current.getSchema("filter.play_count");
    expect(fields).toHaveLength(2);
    expect(fields[0].key).toBe("min_plays");
    expect(fields[1].key).toBe("period");
  });

  it("getFieldLabel returns label when found", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.getFieldLabel("filter.play_count", "min_plays")).toBe(
      "Minimum Plays",
    );
  });

  it("getFieldLabel returns key as fallback for unknown field", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(
      result.current.getFieldLabel("filter.play_count", "unknown_key"),
    ).toBe("unknown_key");
  });

  it("getFieldLabel returns key as fallback for unknown node type", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.getFieldLabel("nonexistent.type", "some_key")).toBe(
      "some_key",
    );
  });

  it("getOptionLabel returns option label when found", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(
      result.current.getOptionLabel("filter.play_count", "period", "30d"),
    ).toBe("Last 30 Days");
  });

  it("getOptionLabel returns value as fallback for unknown option", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(
      result.current.getOptionLabel(
        "filter.play_count",
        "period",
        "unknown_val",
      ),
    ).toBe("unknown_val");
  });

  it("getOptionLabel returns value as fallback for field without options", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(
      result.current.getOptionLabel(
        "filter.play_count",
        "min_plays",
        "some_val",
      ),
    ).toBe("some_val");
  });

  it("getNodeDescription returns description for known type", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.getNodeDescription("filter.play_count")).toBe(
      "Filter tracks by play count",
    );
  });

  it("getNodeDescription returns empty string for unknown type", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.getNodeDescription("nonexistent.type")).toBe("");
  });

  it("exposes multi_select and task_ref fields with their defaults", async () => {
    const { result } = renderHook(() => useNodeSchemas(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    const fields = result.current.getSchema("enricher.play_history");
    expect(fields.map((f) => f.field_type)).toEqual([
      "multi_select",
      "task_ref",
    ]);
    expect(fields[0].default).toEqual(["total_plays", "last_played_dates"]);
    expect(
      result.current.getOptionLabel(
        "enricher.play_history",
        "metrics",
        "period_plays",
      ),
    ).toBe("Period Plays");
  });
});
