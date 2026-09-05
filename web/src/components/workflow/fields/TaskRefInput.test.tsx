import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ConfigFieldSchema } from "#/api/generated/model";

import { NO_UPSTREAM_MESSAGE, TaskRefInput } from "./TaskRefInput";

const field: ConfigFieldSchema = {
  key: "exclusion_source",
  label: "Exclusion Source",
  field_type: "task_ref",
  required: true,
};

const upstreams = [
  { id: "src_1", label: "liked tracks" },
  { id: "src_2", label: "played tracks" },
];

function renderInput(
  props: Partial<React.ComponentProps<typeof TaskRefInput>>,
) {
  const onChange = vi.fn();
  const onBlur = vi.fn();
  render(
    <TaskRefInput
      field={field}
      value={undefined}
      upstreams={upstreams}
      onChange={onChange}
      onBlur={onBlur}
      fieldId="config-exclusion_source"
      hasError={false}
      errorId="config-exclusion_source-error"
      {...props}
    />,
  );
  return { onChange, onBlur };
}

describe("TaskRefInput", () => {
  it("is disabled with an empty-state hint when the node has no upstreams", () => {
    renderInput({ upstreams: [] });
    const trigger = screen.getByRole("combobox");
    expect(trigger).toBeDisabled();
    expect(trigger).toHaveTextContent(NO_UPSTREAM_MESSAGE);
  });

  it("shows the selected upstream by label", () => {
    renderInput({ value: "src_2" });
    expect(screen.getByRole("combobox")).toHaveTextContent("played tracks");
    expect(screen.getByRole("combobox")).toHaveTextContent("(src_2)");
  });

  it("keeps a value whose edge was removed visible as not connected", () => {
    renderInput({ value: "gone" });
    expect(screen.getByRole("combobox")).toHaveTextContent("gone");
  });

  it("wires aria-invalid and aria-describedby on error", () => {
    renderInput({ hasError: true });
    const trigger = screen.getByRole("combobox");
    expect(trigger).toHaveAttribute("aria-invalid", "true");
    expect(trigger).toHaveAttribute(
      "aria-describedby",
      "config-exclusion_source-error",
    );
  });
});
