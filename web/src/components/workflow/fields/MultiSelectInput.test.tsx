import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ConfigFieldSchema } from "#/api/generated/model";

import { MultiSelectInput, resolveMultiSelectValue } from "./MultiSelectInput";

const field: ConfigFieldSchema = {
  key: "metrics",
  label: "Metrics",
  field_type: "multi_select",
  default: ["total_plays", "last_played_dates"],
  options: [
    { value: "total_plays", label: "Total Plays" },
    { value: "last_played_dates", label: "Last Played" },
    { value: "period_plays", label: "Period Plays" },
  ],
};

function renderInput(value: unknown, hasError = false) {
  const onChange = vi.fn();
  const onBlur = vi.fn();
  render(
    <MultiSelectInput
      field={field}
      value={value}
      onChange={onChange}
      onBlur={onBlur}
      fieldId="config-metrics"
      hasError={hasError}
      errorId="config-metrics-error"
    />,
  );
  return { onChange, onBlur };
}

describe("resolveMultiSelectValue", () => {
  it("falls back to the declared default when the key is absent", () => {
    expect(resolveMultiSelectValue(field, undefined)).toEqual([
      "total_plays",
      "last_played_dates",
    ]);
  });

  it("uses the config value when present, even if empty", () => {
    expect(resolveMultiSelectValue(field, [])).toEqual([]);
    expect(resolveMultiSelectValue(field, ["period_plays"])).toEqual([
      "period_plays",
    ]);
  });

  it("is empty for a field without a default", () => {
    expect(
      resolveMultiSelectValue({ ...field, default: undefined }, undefined),
    ).toEqual([]);
  });
});

describe("MultiSelectInput", () => {
  it("pre-checks the default without writing to config", () => {
    const { onChange } = renderInput(undefined);
    expect(screen.getByLabelText("Total Plays")).toBeChecked();
    expect(screen.getByLabelText("Last Played")).toBeChecked();
    expect(screen.getByLabelText("Period Plays")).not.toBeChecked();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("emits the full selection in option order on toggle", async () => {
    const user = userEvent.setup();
    const { onChange, onBlur } = renderInput(["last_played_dates"]);

    await user.click(screen.getByLabelText("Total Plays"));

    expect(onChange).toHaveBeenCalledWith("metrics", [
      "total_plays",
      "last_played_dates",
    ]);
    expect(onBlur).toHaveBeenCalledWith("metrics");
  });

  it("emits undefined when the last option is unchecked", async () => {
    const user = userEvent.setup();
    const { onChange } = renderInput(["period_plays"]);

    await user.click(screen.getByLabelText("Period Plays"));

    expect(onChange).toHaveBeenCalledWith("metrics", undefined);
  });

  it("exposes a labelled group with error wiring", () => {
    renderInput(["period_plays"], true);
    const group = screen.getByRole("group");
    expect(group).toHaveAttribute("aria-labelledby", "config-metrics-label");
    expect(group).toHaveAttribute("aria-invalid", "true");
    expect(group).toHaveAttribute("aria-describedby", "config-metrics-error");
  });
});
