import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useNodeStatuses } from "./useNodeStatuses";

describe("useNodeStatuses", () => {
  it("starts with an empty Map", () => {
    const { result } = renderHook(() => useNodeStatuses());

    expect(result.current.nodeStatuses).toBeInstanceOf(Map);
    expect(result.current.nodeStatuses.size).toBe(0);
  });

  it("adds a node status from snake_case SSE data", () => {
    const { result } = renderHook(() => useNodeStatuses());

    act(() => {
      result.current.handleNodeStatusEvent({
        node_id: "src_1",
        node_type: "source.liked_tracks",
        status: "running",
        execution_order: 1,
        total_nodes: 3,
      });
    });

    const status = result.current.nodeStatuses.get("src_1");
    expect(status).toEqual({
      nodeId: "src_1",
      nodeType: "source.liked_tracks",
      status: "running",
      executionOrder: 1,
      totalNodes: 3,
      durationMs: undefined,
      inputTrackCount: undefined,
      outputTrackCount: undefined,
      errorMessage: undefined,
    });
  });

  it("updates existing node status in-place", () => {
    const { result } = renderHook(() => useNodeStatuses());

    act(() => {
      result.current.handleNodeStatusEvent({
        node_id: "src_1",
        node_type: "source.liked_tracks",
        status: "running",
        execution_order: 1,
        total_nodes: 2,
      });
    });

    act(() => {
      result.current.handleNodeStatusEvent({
        node_id: "src_1",
        node_type: "source.liked_tracks",
        status: "completed",
        execution_order: 1,
        total_nodes: 2,
        duration_ms: 350,
        output_track_count: 40,
      });
    });

    expect(result.current.nodeStatuses.size).toBe(1);
    const status = result.current.nodeStatuses.get("src_1");
    expect(status?.status).toBe("completed");
    expect(status?.durationMs).toBe(350);
    expect(status?.outputTrackCount).toBe(40);
  });

  it("tracks multiple nodes independently", () => {
    const { result } = renderHook(() => useNodeStatuses());

    act(() => {
      result.current.handleNodeStatusEvent({
        node_id: "src_1",
        node_type: "source.liked_tracks",
        status: "completed",
        execution_order: 1,
        total_nodes: 2,
      });
      result.current.handleNodeStatusEvent({
        node_id: "filter_1",
        node_type: "filter.play_count",
        status: "running",
        execution_order: 2,
        total_nodes: 2,
      });
    });

    expect(result.current.nodeStatuses.size).toBe(2);
    expect(result.current.nodeStatuses.get("src_1")?.status).toBe("completed");
    expect(result.current.nodeStatuses.get("filter_1")?.status).toBe("running");
  });

  it("resetNodeStatuses clears the Map", () => {
    const { result } = renderHook(() => useNodeStatuses());

    act(() => {
      result.current.handleNodeStatusEvent({
        node_id: "src_1",
        node_type: "source.liked_tracks",
        status: "completed",
        execution_order: 1,
        total_nodes: 1,
      });
    });

    expect(result.current.nodeStatuses.size).toBe(1);

    act(() => {
      result.current.resetNodeStatuses();
    });

    expect(result.current.nodeStatuses.size).toBe(0);
  });

  it("maps optional fields correctly", () => {
    const { result } = renderHook(() => useNodeStatuses());

    act(() => {
      result.current.handleNodeStatusEvent({
        node_id: "enrich_1",
        node_type: "enricher.lastfm",
        status: "failed",
        execution_order: 2,
        total_nodes: 3,
        duration_ms: 1200,
        input_track_count: 50,
        output_track_count: 48,
        error_message: "Rate limited",
      });
    });

    const status = result.current.nodeStatuses.get("enrich_1");
    expect(status?.durationMs).toBe(1200);
    expect(status?.inputTrackCount).toBe(50);
    expect(status?.outputTrackCount).toBe(48);
    expect(status?.errorMessage).toBe("Rate limited");
  });
});
