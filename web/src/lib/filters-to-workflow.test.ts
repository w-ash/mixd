import { describe, expect, it } from "vitest";
import type { WorkflowDefSchemaInput } from "#/api/generated/model/workflowDefSchemaInput";
import type { WorkflowTaskDefSchemaInput } from "#/api/generated/model/workflowTaskDefSchemaInput";
import {
  filtersToWorkflowDef,
  hasActiveFilters,
  summarizeFilters,
  toWorkflowId,
} from "./filters-to-workflow";

/** Serializer always populates tasks; helpers narrow the generated optional type. */
function tasksOf(wf: WorkflowDefSchemaInput): WorkflowTaskDefSchemaInput[] {
  if (!wf.tasks) throw new Error("expected serialized workflow to have tasks");
  return wf.tasks;
}

function findTask(
  wf: WorkflowDefSchemaInput,
  type: string,
): WorkflowTaskDefSchemaInput {
  const task = tasksOf(wf).find((t) => t.type === type);
  if (!task) throw new Error(`no task of type ${type} in workflow`);
  return task;
}

describe("hasActiveFilters", () => {
  it("is false for an empty state", () => {
    expect(hasActiveFilters({})).toBe(false);
  });

  it("is true when any filter is set", () => {
    expect(hasActiveFilters({ preference: "star" })).toBe(true);
    expect(hasActiveFilters({ tags: ["mood:chill"] })).toBe(true);
    expect(hasActiveFilters({ liked: true })).toBe(true);
    expect(hasActiveFilters({ liked: false })).toBe(true);
    expect(hasActiveFilters({ connector: "spotify" })).toBe(true);
  });

  it("is false when tags is an empty array", () => {
    expect(hasActiveFilters({ tags: [] })).toBe(false);
  });
});

describe("toWorkflowId", () => {
  it("produces a stable slug from a display name", () => {
    expect(toWorkflowId("My Starred Chill Mix")).toBe("my_starred_chill_mix");
  });

  it("collapses runs of non-alnum characters", () => {
    expect(toWorkflowId("Hi -- there!!!")).toBe("hi_there");
  });

  it("strips leading/trailing underscores", () => {
    expect(toWorkflowId("!!! hi !!!")).toBe("hi");
  });

  it("falls back when the name collapses to empty", () => {
    expect(toWorkflowId("!!!")).toBe("saved_filter");
  });
});

describe("filtersToWorkflowDef", () => {
  it("preference-only → source.preferred_tracks + limit + destination", () => {
    const wf = filtersToWorkflowDef(
      { preference: "star" },
      { name: "Starred" },
    );

    expect(wf.id).toBe("starred");
    expect(wf.name).toBe("Starred");
    expect(tasksOf(wf)).toHaveLength(3);

    const [source, limit, destination] = tasksOf(wf);
    expect(source).toMatchObject({
      id: "source",
      type: "source.preferred_tracks",
      config: { state: "star", limit: 100 },
    });
    expect(limit).toMatchObject({
      id: "limit",
      type: "selector.limit_tracks",
      upstream: ["source"],
    });
    expect(destination).toMatchObject({
      id: "create_playlist",
      type: "destination.create_playlist",
      upstream: ["limit"],
    });
    expect((destination?.config ?? {}).connector).toBe("spotify");
  });

  it("no preference → source.liked_tracks with connector filter", () => {
    const wf = filtersToWorkflowDef(
      { connector: "spotify" },
      { name: "All Liked" },
    );

    const source = tasksOf(wf)[0];
    expect(source.type).toBe("source.liked_tracks");
    expect(source.config).toMatchObject({ connector_filter: "spotify" });
  });

  it("tag-only → liked_tracks + enricher.tags + filter.by_tag + limit + destination", () => {
    const wf = filtersToWorkflowDef(
      { tags: ["mood:chill"], tagMode: "and" },
      { name: "Chill Mix" },
    );

    expect(tasksOf(wf)).toHaveLength(5);
    expect(tasksOf(wf).map((t) => t.type)).toEqual([
      "source.liked_tracks",
      "enricher.tags",
      "filter.by_tag",
      "selector.limit_tracks",
      "destination.create_playlist",
    ]);

    const filter = findTask(wf, "filter.by_tag");
    expect(filter.config).toMatchObject({
      tags: ["mood:chill"],
      match_mode: "all",
    });
    expect(filter.upstream).toEqual(["enrich_tags"]);
  });

  it("tag OR mode translates to match_mode=any", () => {
    const wf = filtersToWorkflowDef(
      { tags: ["mood:chill", "mood:melancholy"], tagMode: "or" },
      { name: "Mellow" },
    );
    expect(findTask(wf, "filter.by_tag").config).toMatchObject({
      match_mode: "any",
    });
  });

  it("preference + tags compose: preferred_tracks → tags → filter → limit → dest", () => {
    const wf = filtersToWorkflowDef(
      { preference: "star", tags: ["mood:chill"], tagMode: "and" },
      { name: "Starred Chill" },
    );

    expect(tasksOf(wf).map((t) => t.type)).toEqual([
      "source.preferred_tracks",
      "enricher.tags",
      "filter.by_tag",
      "selector.limit_tracks",
      "destination.create_playlist",
    ]);
    expect(findTask(wf, "enricher.tags").upstream).toEqual(["source"]);
    expect(findTask(wf, "selector.limit_tracks").upstream).toEqual([
      "filter_tags",
    ]);
  });

  it("honors a custom limit", () => {
    const wf = filtersToWorkflowDef(
      { preference: "yah", limit: 25 },
      { name: "Top 25" },
    );
    expect((tasksOf(wf)[0].config ?? {}).limit).toBe(25);
    expect(findTask(wf, "selector.limit_tracks").config).toMatchObject({
      count: 25,
    });
  });

  it("destination name defaults to `{name} {date}` template", () => {
    const wf = filtersToWorkflowDef(
      { preference: "star" },
      { name: "Starred" },
    );
    expect(findTask(wf, "destination.create_playlist").config).toMatchObject({
      name: "Starred {date}",
    });
  });

  it("destination description includes {track_count} template", () => {
    const wf = filtersToWorkflowDef(
      { preference: "star" },
      { name: "Starred" },
    );
    expect(
      String(findTask(wf, "destination.create_playlist").config?.description),
    ).toContain("{track_count}");
  });

  it("custom description overrides the summary", () => {
    const wf = filtersToWorkflowDef(
      { preference: "star" },
      { name: "Starred", description: "My curated best" },
    );
    expect(wf.description).toBe("My curated best");
    expect(
      findTask(wf, "destination.create_playlist").config?.description,
    ).toBe("My curated best");
  });

  it("liked=true with no preference → source.liked_tracks (no special config)", () => {
    const wf = filtersToWorkflowDef({ liked: true }, { name: "Liked Only" });
    expect(tasksOf(wf)[0].type).toBe("source.liked_tracks");
  });
});

describe("summarizeFilters", () => {
  it("returns a generic label for an empty state", () => {
    expect(summarizeFilters({})).toBe("Saved filter");
  });

  it("lists each active filter", () => {
    const summary = summarizeFilters({
      preference: "star",
      tags: ["mood:chill", "energy:low"],
      tagMode: "and",
      liked: true,
      connector: "spotify",
    });
    expect(summary).toContain("preference=star");
    expect(summary).toContain("mood:chill AND energy:low");
    expect(summary).toContain("liked");
    expect(summary).toContain("connector=spotify");
  });

  it("uses OR joiner for tagMode=or", () => {
    const summary = summarizeFilters({
      tags: ["mood:chill", "mood:melancholy"],
      tagMode: "or",
    });
    expect(summary).toContain("mood:chill OR mood:melancholy");
  });

  it("appends an optional suffix", () => {
    expect(
      summarizeFilters({ preference: "star" }, "({track_count} tracks)"),
    ).toContain("({track_count} tracks)");
  });
});
