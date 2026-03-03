import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { usePagination } from "./usePagination";

function wrapper(initialPath = "/") {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <MemoryRouter initialEntries={[initialPath]}>{children}</MemoryRouter>
    );
  };
}

describe("usePagination", () => {
  it("returns page 1 with offset 0 when no URL param", () => {
    const { result } = renderHook(() => usePagination(100), {
      wrapper: wrapper(),
    });

    expect(result.current.page).toBe(1);
    expect(result.current.offset).toBe(0);
    expect(result.current.limit).toBe(50);
    expect(result.current.totalPages).toBe(2);
  });

  it("reads explicit page from URL", () => {
    const { result } = renderHook(() => usePagination(150), {
      wrapper: wrapper("/?page=2"),
    });

    expect(result.current.page).toBe(2);
    expect(result.current.offset).toBe(50);
    expect(result.current.totalPages).toBe(3);
  });

  it("clamps display page when URL page exceeds totalPages", () => {
    const { result } = renderHook(() => usePagination(100), {
      wrapper: wrapper("/?page=99"),
    });

    // Display page clamped to totalPages
    expect(result.current.page).toBe(2);
    // Offset uses raw page (for deep-link correctness before total is known)
    expect(result.current.offset).toBe((99 - 1) * 50);
  });

  it("clamps negative page to 1", () => {
    const { result } = renderHook(() => usePagination(100), {
      wrapper: wrapper("/?page=-5"),
    });

    expect(result.current.page).toBe(1);
    expect(result.current.offset).toBe(0);
  });

  it("treats NaN page as 1", () => {
    const { result } = renderHook(() => usePagination(100), {
      wrapper: wrapper("/?page=abc"),
    });

    expect(result.current.page).toBe(1);
    expect(result.current.offset).toBe(0);
  });

  it("returns totalPages=1 when total is 0", () => {
    const { result } = renderHook(() => usePagination(0), {
      wrapper: wrapper(),
    });

    expect(result.current.totalPages).toBe(1);
    expect(result.current.page).toBe(1);
  });

  it("respects custom defaultLimit", () => {
    const { result } = renderHook(
      () => usePagination(100, { defaultLimit: 25 }),
      { wrapper: wrapper("/?page=3") },
    );

    expect(result.current.limit).toBe(25);
    expect(result.current.totalPages).toBe(4);
    expect(result.current.offset).toBe(50); // (3 - 1) * 25
  });

  it("setPage updates URL param for page > 1", () => {
    const { result } = renderHook(() => usePagination(200), {
      wrapper: wrapper(),
    });

    act(() => {
      result.current.setPage(3);
    });

    expect(result.current.page).toBe(3);
    expect(result.current.offset).toBe(100);
  });

  it("setPage removes param when navigating to page 1", () => {
    const { result } = renderHook(() => usePagination(200), {
      wrapper: wrapper("/?page=3"),
    });

    act(() => {
      result.current.setPage(1);
    });

    expect(result.current.page).toBe(1);
    expect(result.current.offset).toBe(0);
  });
});
