import { afterEach, describe, expect, it, vi } from "vitest";

// vi.hoisted runs before vi.mock hoisting — safe to reference in factory
const { mockUseAuthenticate } = vi.hoisted(() => ({
  mockUseAuthenticate: vi.fn(),
}));

vi.mock("@neondatabase/auth/react/ui", () => ({
  useAuthenticate: mockUseAuthenticate,
}));

import { renderWithProviders, screen } from "@/test/test-utils";

import { AuthGuard } from "./AuthGuard";

describe("AuthGuard", () => {
  afterEach(() => {
    mockUseAuthenticate.mockReset();
  });

  it("renders skeleton while authentication is pending", () => {
    mockUseAuthenticate.mockReturnValue({
      data: undefined,
      isPending: true,
      error: null,
    });

    renderWithProviders(
      <AuthGuard>
        <div>Protected content</div>
      </AuthGuard>,
    );

    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows error alert when auth fails", () => {
    mockUseAuthenticate.mockReturnValue({
      data: null,
      isPending: false,
      error: new Error("Network timeout"),
    });

    renderWithProviders(
      <AuthGuard>
        <div>Protected content</div>
      </AuthGuard>,
    );

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Authentication error")).toBeInTheDocument();
    expect(screen.getByText("Network timeout")).toBeInTheDocument();
    expect(screen.getByText("Try again")).toBeInTheDocument();
  });

  it("redirects to sign-in when not authenticated", () => {
    mockUseAuthenticate.mockReturnValue({
      data: null,
      isPending: false,
      error: null,
    });

    renderWithProviders(
      <AuthGuard>
        <div>Protected content</div>
      </AuthGuard>,
      { routerProps: { initialEntries: ["/"] } },
    );

    expect(screen.queryByText("Protected content")).not.toBeInTheDocument();
  });

  it("renders children when authenticated", () => {
    mockUseAuthenticate.mockReturnValue({
      data: { sub: "user-123" },
      isPending: false,
      error: null,
    });

    renderWithProviders(
      <AuthGuard>
        <div>Protected content</div>
      </AuthGuard>,
    );

    expect(screen.getByText("Protected content")).toBeInTheDocument();
  });
});
