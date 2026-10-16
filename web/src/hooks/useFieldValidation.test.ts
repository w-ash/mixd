import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConfigFieldSchema } from "#/api/generated/model";

import { useFieldValidation } from "./useFieldValidation";

// ─── Test fixtures ──────────────────────────────────────────────

const requiredStringField: ConfigFieldSchema = {
  key: "name",
  label: "Name",
  field_type: "string",
  required: true,
};

const requiredSelectField: ConfigFieldSchema = {
  key: "connector",
  label: "Connector",
  field_type: "select",
  required: true,
  options: [
    { value: "spotify", label: "Spotify" },
    { value: "lastfm", label: "Last.fm" },
  ],
};

const numberField: ConfigFieldSchema = {
  key: "limit",
  label: "Limit",
  field_type: "number",
  required: false,
  min: 1,
  max: 100,
};

const optionalField: ConfigFieldSchema = {
  key: "description",
  label: "Description",
  field_type: "string",
  required: false,
};

const schema: ConfigFieldSchema[] = [
  requiredStringField,
  requiredSelectField,
  numberField,
  optionalField,
];

const taskRefField: ConfigFieldSchema = {
  key: "exclusion_source",
  label: "Exclusion Source",
  field_type: "task_ref",
  required: true,
};

const primaryInputField: ConfigFieldSchema = {
  key: "primary_input",
  label: "Primary Input",
  field_type: "task_ref",
  required: false,
};

const multiSelectField: ConfigFieldSchema = {
  key: "metrics",
  label: "Metrics",
  field_type: "multi_select",
  required: false,
  default: ["total_plays"],
  options: [
    { value: "total_plays", label: "Total Plays" },
    { value: "period_plays", label: "Period Plays" },
  ],
};

const upstreams = [
  { id: "src_1", label: "liked tracks" },
  { id: "src_2", label: "played tracks" },
];

// ─── Tests ──────────────────────────────────────────────────────

describe("useFieldValidation", () => {
  it("initially has no errors", () => {
    const { result } = renderHook(() =>
      useFieldValidation(schema, { name: "test", connector: "spotify" }, "n1"),
    );

    expect(result.current.hasErrors).toBe(false);
    expect(result.current.getError("name")).toBeUndefined();
    expect(result.current.getError("connector")).toBeUndefined();
  });

  it("attemptSave returns errors for missing required fields", () => {
    const { result } = renderHook(() =>
      useFieldValidation(schema, { name: "", connector: "" }, "n1"),
    );

    let errors = new Map<string, string>();
    act(() => {
      errors = result.current.attemptSave();
    });

    expect(errors.size).toBe(2);
    expect(errors.get("name")).toBe("Name is required");
    expect(errors.get("connector")).toBe("Connector is required");
    expect(result.current.hasErrors).toBe(true);
    expect(result.current.getError("name")).toBe("Name is required");
    expect(result.current.getError("connector")).toBe("Connector is required");
  });

  it("attemptSave returns no errors when all required fields are valid", () => {
    const { result } = renderHook(() =>
      useFieldValidation(
        schema,
        { name: "My Workflow", connector: "spotify", limit: 50 },
        "n1",
      ),
    );

    let errors = new Map<string, string>();
    act(() => {
      errors = result.current.attemptSave();
    });

    expect(errors.size).toBe(0);
    expect(result.current.hasErrors).toBe(false);
  });

  it("blurField does not show error before save attempt", () => {
    const { result } = renderHook(() =>
      useFieldValidation(schema, { name: "" }, "n1"),
    );

    act(() => {
      result.current.blurField("name");
    });

    // Before attemptSave, blur on a field without prior error should not trigger
    expect(result.current.getError("name")).toBeUndefined();
    expect(result.current.hasErrors).toBe(false);
  });

  it("after a save attempt, blur re-validates the field's current value", () => {
    const { result, rerender } = renderHook(
      ({ config }) => useFieldValidation(schema, config, "n1"),
      {
        initialProps: {
          config: { name: "My Workflow", connector: "spotify" } as Record<
            string,
            unknown
          >,
        },
      },
    );

    act(() => {
      result.current.attemptSave();
    });
    expect(result.current.hasErrors).toBe(false);

    // Cleared after the save: the blur shows it without waiting for another save.
    rerender({ config: { name: "", connector: "spotify" } });
    act(() => {
      result.current.blurField("name");
    });
    expect(result.current.getError("name")).toBe("Name is required");

    // Fixed again: the next blur clears it.
    rerender({ config: { name: "Renamed", connector: "spotify" } });
    act(() => {
      result.current.blurField("name");
    });
    expect(result.current.getError("name")).toBeUndefined();
    expect(result.current.hasErrors).toBe(false);
  });

  it("changeField clears error when value is corrected", () => {
    const { result } = renderHook(() =>
      useFieldValidation(schema, { name: "", connector: "spotify" }, "n1"),
    );

    // Trigger save to set errors
    act(() => {
      result.current.attemptSave();
    });

    expect(result.current.getError("name")).toBe("Name is required");

    // Whitespace is still blank: the error stays.
    act(() => {
      result.current.changeField("name", "   ");
    });
    expect(result.current.getError("name")).toBe("Name is required");

    act(() => {
      result.current.changeField("name", "My Workflow");
    });

    expect(result.current.getError("name")).toBeUndefined();
    expect(result.current.hasErrors).toBe(false);
  });

  it("changeField does not set error on field without prior error", () => {
    const { result } = renderHook(() =>
      useFieldValidation(schema, { name: "valid", connector: "spotify" }, "n1"),
    );

    // Change a valid field to invalid — should not show error before save
    act(() => {
      result.current.changeField("name", "");
    });

    expect(result.current.getError("name")).toBeUndefined();
  });

  // Bounds are inclusive: the declared min and max are themselves valid.
  it.each([
    [0, "Must be at least 1"],
    [1, undefined],
    [100, undefined],
    [101, "Must be at most 100"],
  ])("limit %i against min 1 / max 100 → %s", (limit, expected) => {
    const { result } = renderHook(() =>
      useFieldValidation(
        schema,
        { name: "test", connector: "spotify", limit },
        "n1",
      ),
    );

    let errors = new Map<string, string>();
    act(() => {
      errors = result.current.attemptSave();
    });

    expect(errors.get("limit")).toBe(expected);
    expect(result.current.getError("limit")).toBe(expected);
  });

  it("resets errors when selectedNodeId changes", () => {
    const { result, rerender } = renderHook(
      ({ nodeId }) =>
        useFieldValidation(schema, { name: "", connector: "" }, nodeId),
      { initialProps: { nodeId: "n1" as string | null } },
    );

    // Set errors via save
    act(() => {
      result.current.attemptSave();
    });

    expect(result.current.hasErrors).toBe(true);

    // Switch node
    rerender({ nodeId: "n2" });

    expect(result.current.hasErrors).toBe(false);
    expect(result.current.getError("name")).toBeUndefined();
  });

  describe("task_ref", () => {
    it("requires a value on a required task_ref", () => {
      const { result } = renderHook(() =>
        useFieldValidation([taskRefField], {}, "n1", upstreams),
      );

      let errors = new Map<string, string>();
      act(() => {
        errors = result.current.attemptSave();
      });

      expect(errors.get("exclusion_source")).toBe(
        "Exclusion Source is required",
      );
    });

    it("rejects a value that is not one of the node's upstreams", () => {
      const { result } = renderHook(() =>
        useFieldValidation(
          [taskRefField, primaryInputField],
          { exclusion_source: "elsewhere", primary_input: "src_2" },
          "n1",
          upstreams,
        ),
      );

      let errors = new Map<string, string>();
      act(() => {
        errors = result.current.attemptSave();
      });

      expect(errors.get("exclusion_source")).toBe(
        "Must be one of this node's upstream tasks",
      );
      expect(errors.get("primary_input")).toBeUndefined();
    });

    it("accepts an absent optional task_ref", () => {
      const { result } = renderHook(() =>
        useFieldValidation([primaryInputField], {}, "n1", upstreams),
      );

      let errors = new Map<string, string>();
      act(() => {
        errors = result.current.attemptSave();
      });

      expect(errors.size).toBe(0);
    });

    it("clears the error once the field is corrected", () => {
      const { result } = renderHook(() =>
        useFieldValidation(
          [taskRefField],
          { exclusion_source: "elsewhere" },
          "n1",
          upstreams,
        ),
      );

      act(() => {
        result.current.attemptSave();
      });
      expect(result.current.getError("exclusion_source")).toBe(
        "Must be one of this node's upstream tasks",
      );

      act(() => {
        result.current.changeField("exclusion_source", "src_1");
      });
      expect(result.current.getError("exclusion_source")).toBeUndefined();
    });
  });

  describe("multi_select", () => {
    it("requires a non-empty array on a required multi_select", () => {
      const required = { ...multiSelectField, required: true };
      const { result } = renderHook(() =>
        useFieldValidation([required], { metrics: [] }, "n1"),
      );

      let errors = new Map<string, string>();
      act(() => {
        errors = result.current.attemptSave();
      });

      expect(errors.get("metrics")).toBe("Metrics is required");
    });

    it("accepts an absent optional multi_select (declared default applies)", () => {
      const { result } = renderHook(() =>
        useFieldValidation([multiSelectField], {}, "n1"),
      );

      let errors = new Map<string, string>();
      act(() => {
        errors = result.current.attemptSave();
      });

      expect(errors.size).toBe(0);
    });

    it("rejects elements outside the declared options", () => {
      const { result } = renderHook(() =>
        useFieldValidation(
          [multiSelectField],
          { metrics: ["total_plays", "bogus"] },
          "n1",
        ),
      );

      let errors = new Map<string, string>();
      act(() => {
        errors = result.current.attemptSave();
      });

      expect(errors.get("metrics")).toBe("Unknown option: bogus");
    });

    it("accepts a subset of the declared options", () => {
      const { result } = renderHook(() =>
        useFieldValidation(
          [multiSelectField],
          { metrics: ["period_plays"] },
          "n1",
        ),
      );

      let errors = new Map<string, string>();
      act(() => {
        errors = result.current.attemptSave();
      });

      expect(errors.size).toBe(0);
    });
  });
});
