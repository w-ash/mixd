import { describe, expect, it } from "vitest";
import type { WorkflowTaskDefSchemaInput } from "#/api/generated/model";
import { diffWorkflowDefs } from "#/lib/workflow-diff";

function makeTask(
  overrides: Partial<WorkflowTaskDefSchemaInput> & { id: string },
): WorkflowTaskDefSchemaInput {
  return {
    type: "filter",
    ...overrides,
  };
}

describe("diffWorkflowDefs", () => {
  it("marks all tasks unchanged when definitions are identical", () => {
    const tasks: WorkflowTaskDefSchemaInput[] = [
      makeTask({ id: "a", type: "source", config: { service: "spotify" } }),
      makeTask({
        id: "b",
        type: "filter",
        upstream: ["a"],
        config: { min_plays: 5 },
      }),
    ];

    const diff = diffWorkflowDefs(tasks, tasks);

    expect(diff.added).toHaveLength(0);
    expect(diff.removed).toHaveLength(0);
    expect(diff.modified).toHaveLength(0);
    expect(diff.unchanged).toHaveLength(2);
  });

  it("detects added tasks present in new but not old", () => {
    const oldTasks = [makeTask({ id: "a", type: "source" })];
    const newTasks = [
      makeTask({ id: "a", type: "source" }),
      makeTask({ id: "b", type: "filter", upstream: ["a"] }),
    ];

    const diff = diffWorkflowDefs(oldTasks, newTasks);

    expect(diff.added).toHaveLength(1);
    expect(diff.added[0].id).toBe("b");
    expect(diff.removed).toHaveLength(0);
    expect(diff.modified).toHaveLength(0);
    expect(diff.unchanged).toHaveLength(1);
  });

  it("detects removed tasks present in old but not new", () => {
    const oldTasks = [
      makeTask({ id: "a", type: "source" }),
      makeTask({ id: "b", type: "filter", upstream: ["a"] }),
    ];
    const newTasks = [makeTask({ id: "a", type: "source" })];

    const diff = diffWorkflowDefs(oldTasks, newTasks);

    expect(diff.removed).toHaveLength(1);
    expect(diff.removed[0].id).toBe("b");
    expect(diff.added).toHaveLength(0);
    expect(diff.modified).toHaveLength(0);
    expect(diff.unchanged).toHaveLength(1);
  });

  it("detects modified tasks when config differs", () => {
    const oldTasks = [
      makeTask({ id: "a", type: "filter", config: { min_plays: 5 } }),
    ];
    const newTasks = [
      makeTask({ id: "a", type: "filter", config: { min_plays: 10 } }),
    ];

    const diff = diffWorkflowDefs(oldTasks, newTasks);

    expect(diff.modified).toHaveLength(1);
    expect(diff.modified[0].old.config).toEqual({ min_plays: 5 });
    expect(diff.modified[0].new.config).toEqual({ min_plays: 10 });
    expect(diff.unchanged).toHaveLength(0);
  });

  it("detects modified tasks when type differs", () => {
    const oldTasks = [makeTask({ id: "a", type: "filter" })];
    const newTasks = [makeTask({ id: "a", type: "sort" })];

    const diff = diffWorkflowDefs(oldTasks, newTasks);

    expect(diff.modified).toHaveLength(1);
    expect(diff.modified[0].old.type).toBe("filter");
    expect(diff.modified[0].new.type).toBe("sort");
  });

  it("detects modified tasks when upstream differs", () => {
    const oldTasks = [makeTask({ id: "a", type: "filter", upstream: ["x"] })];
    const newTasks = [
      makeTask({ id: "a", type: "filter", upstream: ["x", "y"] }),
    ];

    const diff = diffWorkflowDefs(oldTasks, newTasks);

    expect(diff.modified).toHaveLength(1);
    expect(diff.modified[0].old.upstream).toEqual(["x"]);
    expect(diff.modified[0].new.upstream).toEqual(["x", "y"]);
  });

  it("treats upstream order as irrelevant", () => {
    const oldTasks = [
      makeTask({ id: "a", type: "filter", upstream: ["y", "x"] }),
    ];
    const newTasks = [
      makeTask({ id: "a", type: "filter", upstream: ["x", "y"] }),
    ];

    const diff = diffWorkflowDefs(oldTasks, newTasks);

    expect(diff.modified).toHaveLength(0);
    expect(diff.unchanged).toHaveLength(1);
  });

  it("handles a complex diff with all change types at once", () => {
    const oldTasks = [
      makeTask({ id: "kept", type: "source", config: { service: "spotify" } }),
      makeTask({ id: "changed", type: "filter", config: { min_plays: 5 } }),
      makeTask({ id: "gone", type: "sort", upstream: ["kept"] }),
    ];
    const newTasks = [
      makeTask({ id: "kept", type: "source", config: { service: "spotify" } }),
      makeTask({ id: "changed", type: "filter", config: { min_plays: 20 } }),
      makeTask({ id: "fresh", type: "enrich", upstream: ["kept"] }),
    ];

    const diff = diffWorkflowDefs(oldTasks, newTasks);

    expect(diff.unchanged).toHaveLength(1);
    expect(diff.unchanged[0].id).toBe("kept");

    expect(diff.modified).toHaveLength(1);
    expect(diff.modified[0].old.id).toBe("changed");

    expect(diff.removed).toHaveLength(1);
    expect(diff.removed[0].id).toBe("gone");

    expect(diff.added).toHaveLength(1);
    expect(diff.added[0].id).toBe("fresh");
  });

  it("produces an empty diff for empty task lists", () => {
    const diff = diffWorkflowDefs([], []);

    expect(diff.added).toHaveLength(0);
    expect(diff.removed).toHaveLength(0);
    expect(diff.modified).toHaveLength(0);
    expect(diff.unchanged).toHaveLength(0);
    expect(diff.highlightMap.size).toBe(0);
  });

  it("populates highlightMap with correct status for every task ID", () => {
    const oldTasks = [
      makeTask({ id: "same", type: "source" }),
      makeTask({ id: "edited", type: "filter", config: { n: 1 } }),
      makeTask({ id: "dropped", type: "sort" }),
    ];
    const newTasks = [
      makeTask({ id: "same", type: "source" }),
      makeTask({ id: "edited", type: "filter", config: { n: 99 } }),
      makeTask({ id: "new-node", type: "enrich" }),
    ];

    const { highlightMap } = diffWorkflowDefs(oldTasks, newTasks);

    expect(highlightMap.get("same")).toBe("unchanged");
    expect(highlightMap.get("edited")).toBe("modified");
    expect(highlightMap.get("dropped")).toBe("removed");
    expect(highlightMap.get("new-node")).toBe("added");
    expect(highlightMap.size).toBe(4);
  });
});
