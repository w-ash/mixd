import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ConfigFieldSchema } from "@/api/generated/model";

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

    let errors: Map<string, string>;
    act(() => {
      errors = result.current.attemptSave();
    });

    expect(errors!.size).toBe(2);
    expect(errors!.get("name")).toBe("Name is required");
    expect(errors!.get("connector")).toBe("Connector is required");
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

    let errors: Map<string, string>;
    act(() => {
      errors = result.current.attemptSave();
    });

    expect(errors!.size).toBe(0);
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

  it("after attemptSave, blurField shows validation errors", () => {
    const { result } = renderHook(() =>
      useFieldValidation(schema, { name: "", connector: "spotify" }, "n1"),
    );

    // First trigger save attempt to enable blur validation
    act(() => {
      result.current.attemptSave();
    });

    expect(result.current.getError("name")).toBe("Name is required");

    // Blur on name should still show error (value is still empty)
    act(() => {
      result.current.blurField("name");
    });

    expect(result.current.getError("name")).toBe("Name is required");
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

    // Simulate changing the field to a valid value
    act(() => {
      result.current.changeField("name", "My Workflow");
    });

    expect(result.current.getError("name")).toBeUndefined();
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

  it("number min validation works", () => {
    const { result } = renderHook(() =>
      useFieldValidation(schema, { name: "test", connector: "spotify" }, "n1"),
    );

    // Trigger save first, then set a number error
    act(() => {
      result.current.attemptSave();
    });

    // No error on limit since it's optional and undefined
    expect(result.current.getError("limit")).toBeUndefined();

    // Now let's test with a value below min — need to re-render with bad config
    const { result: result2 } = renderHook(() =>
      useFieldValidation(
        schema,
        { name: "test", connector: "spotify", limit: 0 },
        "n2",
      ),
    );

    let errors: Map<string, string>;
    act(() => {
      errors = result2.current.attemptSave();
    });

    expect(errors!.get("limit")).toBe("Must be at least 1");
    expect(result2.current.getError("limit")).toBe("Must be at least 1");
  });

  it("number max validation works", () => {
    const { result } = renderHook(() =>
      useFieldValidation(
        schema,
        { name: "test", connector: "spotify", limit: 200 },
        "n1",
      ),
    );

    let errors: Map<string, string>;
    act(() => {
      errors = result.current.attemptSave();
    });

    expect(errors!.get("limit")).toBe("Must be at most 100");
    expect(result.current.getError("limit")).toBe("Must be at most 100");
  });

  it("getError returns undefined for valid fields", () => {
    const { result } = renderHook(() =>
      useFieldValidation(
        schema,
        { name: "My Workflow", connector: "spotify", limit: 50 },
        "n1",
      ),
    );

    act(() => {
      result.current.attemptSave();
    });

    expect(result.current.getError("name")).toBeUndefined();
    expect(result.current.getError("connector")).toBeUndefined();
    expect(result.current.getError("limit")).toBeUndefined();
    expect(result.current.getError("description")).toBeUndefined();
    expect(result.current.getError("nonexistent_key")).toBeUndefined();
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
});
