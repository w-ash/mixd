import { describe, expect, it } from "vitest";

import { NodeType } from "#/api/generated/model";

import {
  findNodeCategory,
  formatNodeTypeName,
  getNodeCategory,
  getNodeCategoryName,
  miniMapNodeColor,
  NODE_CONFIG,
  resolveNodeCategory,
} from "./workflow-config";

describe("NODE_CONFIG", () => {
  it("covers every generated node category", () => {
    for (const category of Object.values(NodeType)) {
      expect(NODE_CONFIG[category]).toBeDefined();
    }
    expect(Object.keys(NODE_CONFIG).sort()).toEqual(
      Object.values(NodeType).sort(),
    );
  });
});

describe("getNodeCategoryName", () => {
  it("takes the segment before the first dot", () => {
    expect(getNodeCategoryName("filter.by_metric")).toBe("filter");
  });

  it("returns the whole string when there is no dot", () => {
    expect(getNodeCategoryName("filter")).toBe("filter");
  });
});

describe("findNodeCategory", () => {
  it("resolves a known category", () => {
    expect(findNodeCategory("filter.by_metric")?.label).toBe("Filter");
  });

  it("returns null for an unknown category", () => {
    expect(findNodeCategory("mystery.thing")).toBeNull();
  });
});

describe("getNodeCategory", () => {
  it("falls back to source for an unknown category", () => {
    expect(getNodeCategory("mystery.thing")).toBe(NODE_CONFIG.source);
  });
});

describe("resolveNodeCategory", () => {
  it("prefers the declared category over the dotted node type", () => {
    expect(resolveNodeCategory("filter", "source.liked_tracks")).toBe(
      NODE_CONFIG.filter,
    );
  });

  it("falls back to the node type when nothing is declared", () => {
    expect(resolveNodeCategory(null, "sorter.by_metric")).toBe(
      NODE_CONFIG.sorter,
    );
  });
});

describe("formatNodeTypeName", () => {
  it("humanizes the type suffix", () => {
    expect(formatNodeTypeName("filter.by_metric")).toBe("by metric");
  });

  it("passes through a type with no suffix", () => {
    expect(formatNodeTypeName("filter")).toBe("filter");
  });
});

describe("miniMapNodeColor", () => {
  it("mixes the category accent for a known node type", () => {
    expect(miniMapNodeColor({ type: "filter" })).toContain(
      NODE_CONFIG.filter.accentColor,
    );
  });

  it("returns the neutral fill when the type is unknown", () => {
    expect(miniMapNodeColor({})).toBe("oklch(0.25 0.01 60)");
  });
});
