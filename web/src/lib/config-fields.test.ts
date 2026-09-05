import { describe, expect, it } from "vitest";

import type { ConfigFieldSchema } from "#/api/generated/model";

import { coerceFieldValue, taskRefKeys } from "./config-fields";

const booleanField: ConfigFieldSchema = {
  key: "reverse",
  label: "Highest First",
  field_type: "boolean",
  default: true,
};

const stringField: ConfigFieldSchema = {
  key: "name",
  label: "Name",
  field_type: "string",
};

describe("coerceFieldValue", () => {
  it("maps legacy string booleans to real booleans", () => {
    expect(coerceFieldValue(booleanField, "false")).toBe(false);
    expect(coerceFieldValue(booleanField, "true")).toBe(true);
    expect(coerceFieldValue(booleanField, " TRUE ")).toBe(true);
  });

  it("reads an unrecognised string as unset", () => {
    expect(coerceFieldValue(booleanField, "yes")).toBeUndefined();
  });

  it("passes booleans and other field types through", () => {
    expect(coerceFieldValue(booleanField, false)).toBe(false);
    expect(coerceFieldValue(booleanField, undefined)).toBeUndefined();
    expect(coerceFieldValue(stringField, "false")).toBe("false");
  });
});

describe("taskRefKeys", () => {
  it("returns only the keys declared as task_ref", () => {
    expect(
      taskRefKeys([
        stringField,
        { key: "exclusion_source", label: "X", field_type: "task_ref" },
        booleanField,
        { key: "primary_input", label: "P", field_type: "task_ref" },
      ]),
    ).toEqual(["exclusion_source", "primary_input"]);
  });

  it("is empty for a schema without references", () => {
    expect(taskRefKeys([stringField, booleanField])).toEqual([]);
  });
});
