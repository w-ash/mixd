import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useSelectionSet } from "./useSelectionSet";

const IDS = ["a", "b", "c"];

describe("useSelectionSet", () => {
  it("starts empty with an unchecked header", () => {
    const { result } = renderHook(() => useSelectionSet(IDS));

    expect(result.current.size).toBe(0);
    expect(result.current.headerChecked).toBe(false);
    expect(result.current.isSelected("a")).toBe(false);
  });

  it("toggles an id on and back off", () => {
    const { result } = renderHook(() => useSelectionSet(IDS));

    act(() => result.current.toggle("b"));
    expect(result.current.isSelected("b")).toBe(true);
    expect(result.current.size).toBe(1);
    expect(result.current.headerChecked).toBe("indeterminate");

    act(() => result.current.toggle("b"));
    expect(result.current.isSelected("b")).toBe(false);
    expect(result.current.headerChecked).toBe(false);
  });

  it("honours an explicit checked state from a checkbox", () => {
    const { result } = renderHook(() => useSelectionSet(IDS));

    act(() => result.current.toggle("a", true));
    act(() => result.current.toggle("a", true));
    expect(result.current.size).toBe(1);

    act(() => result.current.toggle("a", false));
    expect(result.current.size).toBe(0);
  });

  it("skips the re-render when a toggle changes nothing", () => {
    const { result } = renderHook(() => useSelectionSet(IDS));

    act(() => result.current.toggle("a", true));
    const before = result.current.selected;

    // Radix re-fires onCheckedChange with the state the set already holds.
    act(() => result.current.toggle("a", true));
    expect(result.current.selected).toBe(before);

    act(() => result.current.toggle("c", false));
    expect(result.current.selected).toBe(before);
  });

  it("selects every visible id, then clears them, via toggleAll", () => {
    const { result } = renderHook(() => useSelectionSet(IDS));

    act(() => result.current.toggleAll());
    expect(result.current.size).toBe(3);
    expect(result.current.headerChecked).toBe(true);

    act(() => result.current.toggleAll());
    expect(result.current.size).toBe(0);
    expect(result.current.headerChecked).toBe(false);
  });

  it("completes a partial selection rather than clearing it", () => {
    const { result } = renderHook(() => useSelectionSet(IDS));

    act(() => result.current.toggle("a"));
    act(() => result.current.toggleAll());

    expect(result.current.size).toBe(3);
  });

  it("does nothing when toggleAll runs on an empty list", () => {
    const { result } = renderHook(() => useSelectionSet([]));
    const before = result.current.selected;

    act(() => result.current.toggleAll());

    expect(result.current.selected).toBe(before);
    expect(result.current.headerChecked).toBe(false);
  });

  it("hides ids that disappear from the list", () => {
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useSelectionSet(ids),
      { initialProps: { ids: IDS } },
    );

    act(() => result.current.toggleAll());
    expect(result.current.size).toBe(3);

    // A background refetch drops a row: the stale id must not inflate the
    // count, satisfy "select all", or ride along in a batch mutation.
    rerender({ ids: ["a", "b"] });
    expect(result.current.size).toBe(2);
    expect(result.current.isSelected("c")).toBe(false);
    expect(result.current.headerChecked).toBe(true);
  });

  it("restores a selected id that comes back to the list", () => {
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useSelectionSet(ids),
      { initialProps: { ids: IDS } },
    );

    act(() => result.current.toggle("c"));
    rerender({ ids: ["a", "b"] });
    expect(result.current.size).toBe(0);

    rerender({ ids: IDS });
    expect(result.current.isSelected("c")).toBe(true);
  });

  it("clears the selection including ids that are not visible", () => {
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useSelectionSet(ids),
      { initialProps: { ids: IDS } },
    );

    act(() => result.current.toggleAll());
    rerender({ ids: ["a"] });
    act(() => result.current.clear());
    rerender({ ids: IDS });

    expect(result.current.size).toBe(0);
  });

  it("keeps toggle and clear identities stable across id changes", () => {
    // toggleAll is not covered here: it closes over the visible-ids set, which
    // is derived from `ids`, so its identity tracks `ids` — no consumer relies
    // on it staying stable across an id-list change.
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useSelectionSet(ids),
      { initialProps: { ids: IDS } },
    );
    const { toggle, clear } = result.current;

    rerender({ ids: ["a", "b", "c", "d"] });

    expect(result.current.toggle).toBe(toggle);
    expect(result.current.clear).toBe(clear);
  });

  it("applies toggleAll to the ids visible at the time of the call", () => {
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useSelectionSet(ids),
      { initialProps: { ids: IDS } },
    );

    rerender({ ids: ["a", "b", "c", "d"] });
    act(() => result.current.toggleAll());

    expect(result.current.size).toBe(4);
    expect(result.current.isSelected("d")).toBe(true);
  });
});

describe("useSelectionSet — visibleIds", () => {
  it("keeps a selection the filter hides", () => {
    const { result, rerender } = renderHook(
      ({ visible }: { visible: string[] }) =>
        useSelectionSet(IDS, { visibleIds: visible }),
      { initialProps: { visible: ["a", "b"] } },
    );

    act(() => result.current.toggleAll());
    expect(result.current.size).toBe(2);

    // The filter narrows to a row that was never picked: the earlier picks
    // stay in the selection, out of sight.
    rerender({ visible: ["c"] });
    expect(result.current.size).toBe(2);
    expect(result.current.visibleSize).toBe(0);
    expect(result.current.headerChecked).toBe(false);

    act(() => result.current.toggle("c"));
    expect(result.current.size).toBe(3);
  });

  it("scopes toggleAll and headerChecked to the visible ids", () => {
    const { result } = renderHook(() =>
      useSelectionSet(IDS, { visibleIds: ["a", "b"] }),
    );

    act(() => result.current.toggle("a"));
    expect(result.current.headerChecked).toBe("indeterminate");

    act(() => result.current.toggleAll());
    expect(result.current.headerChecked).toBe(true);
    expect(result.current.size).toBe(2);
    expect(result.current.isSelected("c")).toBe(false);

    act(() => result.current.toggleAll());
    expect(result.current.size).toBe(0);
  });

  it("still prunes ids that leave the full list", () => {
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useSelectionSet(ids, { visibleIds: ids }),
      { initialProps: { ids: IDS } },
    );

    act(() => result.current.toggleAll());
    rerender({ ids: ["a", "b"] });

    expect(result.current.size).toBe(2);
    expect(result.current.visibleSize).toBe(2);
    expect(result.current.isSelected("c")).toBe(false);
  });
});
