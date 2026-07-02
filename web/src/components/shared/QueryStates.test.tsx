import { useQuery } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { API_ERROR_CODES, ApiError } from "#/api/client";
import { renderWithProviders, screen } from "#/test/test-utils";

import { QueryStates } from "./QueryStates";

const skeleton = <div data-testid="skeleton-slot" />;
const empty = <div data-testid="empty-slot" />;
const child = <div data-testid="success-slot" />;

describe("QueryStates", () => {
  it("renders the skeleton while loading", () => {
    renderWithProviders(
      <QueryStates
        loading
        isError={false}
        errorHeading="Failed to load things"
        skeleton={skeleton}
        isEmpty
        empty={empty}
      >
        {child}
      </QueryStates>,
    );

    expect(screen.getByTestId("skeleton-slot")).toBeInTheDocument();
    expect(screen.queryByTestId("empty-slot")).not.toBeInTheDocument();
    expect(screen.queryByTestId("success-slot")).not.toBeInTheDocument();
  });

  it("reads only the flag it is given — isPending vs isLoading on a disabled query", () => {
    // A disabled query is the case where the two flags diverge in Tanstack v5:
    // isPending stays true, isLoading (= isPending && isFetching) stays false.
    function Harness({ flag }: { flag: "isPending" | "isLoading" }) {
      const query = useQuery({
        queryKey: ["query-states-disabled"],
        queryFn: () => Promise.resolve([]),
        enabled: false,
      });
      return (
        <QueryStates
          loading={query[flag]}
          isError={query.isError}
          error={query.error}
          errorHeading="Failed to load things"
          skeleton={skeleton}
        >
          {child}
        </QueryStates>
      );
    }

    const { unmount } = renderWithProviders(<Harness flag="isPending" />);
    expect(screen.getByTestId("skeleton-slot")).toBeInTheDocument();
    unmount();

    renderWithProviders(<Harness flag="isLoading" />);
    expect(screen.getByTestId("success-slot")).toBeInTheDocument();
  });

  it("renders the error state with heading and error message", () => {
    renderWithProviders(
      <QueryStates
        loading={false}
        isError
        error={new Error("boom")}
        errorHeading="Failed to load things"
        skeleton={skeleton}
      >
        {child}
      </QueryStates>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Failed to load things");
    expect(alert).toHaveTextContent("boom");
  });

  it("prefers a static errorDescription over the error message", () => {
    renderWithProviders(
      <QueryStates
        loading={false}
        isError
        error={new Error("boom")}
        errorHeading="Couldn't load tags"
        errorDescription="Refresh the page or check your connection."
        skeleton={skeleton}
      >
        {child}
      </QueryStates>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(
      "Refresh the page or check your connection.",
    );
    expect(alert).not.toHaveTextContent("boom");
  });

  it("passes database-unavailable errors through to the dedicated prompt", () => {
    renderWithProviders(
      <QueryStates
        loading={false}
        isError
        error={
          new ApiError(503, API_ERROR_CODES.DATABASE_UNAVAILABLE, "db down")
        }
        errorHeading="Failed to load things"
        skeleton={skeleton}
      >
        {child}
      </QueryStates>,
    );

    expect(screen.getByText("Database unavailable")).toBeInTheDocument();
  });

  it("renders the error state even when isEmpty is set", () => {
    renderWithProviders(
      <QueryStates
        loading={false}
        isError
        error={new Error("boom")}
        errorHeading="Failed to load things"
        skeleton={skeleton}
        isEmpty
        empty={empty}
      >
        {child}
      </QueryStates>,
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("empty-slot")).not.toBeInTheDocument();
  });

  it("renders the empty slot instead of children when isEmpty", () => {
    renderWithProviders(
      <QueryStates
        loading={false}
        isError={false}
        errorHeading="Failed to load things"
        skeleton={skeleton}
        isEmpty
        empty={empty}
      >
        {child}
      </QueryStates>,
    );

    expect(screen.getByTestId("empty-slot")).toBeInTheDocument();
    expect(screen.queryByTestId("success-slot")).not.toBeInTheDocument();
  });

  it("renders children when isEmpty is omitted", () => {
    renderWithProviders(
      <QueryStates
        loading={false}
        isError={false}
        errorHeading="Failed to load things"
        skeleton={skeleton}
      >
        {child}
      </QueryStates>,
    );

    expect(screen.getByTestId("success-slot")).toBeInTheDocument();
  });

  it("renders children on success", () => {
    renderWithProviders(
      <QueryStates
        loading={false}
        isError={false}
        errorHeading="Failed to load things"
        skeleton={skeleton}
        isEmpty={false}
        empty={empty}
      >
        {child}
      </QueryStates>,
    );

    expect(screen.getByTestId("success-slot")).toBeInTheDocument();
    expect(screen.queryByTestId("empty-slot")).not.toBeInTheDocument();
  });
});
