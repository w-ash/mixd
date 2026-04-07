import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// vi.hoisted runs before vi.mock hoisting — safe to reference in factory
const { mockAuthView, mockUseAuthenticate } = vi.hoisted(() => ({
  mockAuthView: vi.fn(),
  mockUseAuthenticate: vi.fn(),
}));

vi.mock("@neondatabase/auth/react/ui", () => ({
  AuthView: (props: { pathname?: string }) => mockAuthView(props),
  useAuthenticate: () => mockUseAuthenticate(),
}));

vi.mock("#/components/shared/MixdLogo", () => ({
  MixdLogo: () => <div data-testid="mixd-logo">MixdLogo</div>,
}));

import { Route, Routes } from "react-router";
import { renderWithProviders, screen } from "#/test/test-utils";

import { Login } from "./Login";

function renderLogin(path: string) {
  return renderWithProviders(
    <Routes>
      <Route path="auth/:pathname" element={<Login />} />
    </Routes>,
    { routerProps: { initialEntries: [`/auth/${path}`] } },
  );
}

describe("Login", () => {
  beforeEach(() => {
    mockAuthView.mockReturnValue(<div data-testid="auth-view">Auth Form</div>);
    mockUseAuthenticate.mockReturnValue({
      data: null,
      isPending: false,
      error: null,
    });
  });

  afterEach(() => {
    mockAuthView.mockReset();
    mockUseAuthenticate.mockReset();
  });

  it("renders logo and sign-in text", () => {
    renderLogin("sign-in");

    expect(screen.getByTestId("mixd-logo")).toBeInTheDocument();
    expect(screen.getByText("Sign in to continue")).toBeInTheDocument();
  });

  it("renders sign-up text for sign-up path", () => {
    renderLogin("sign-up");

    expect(
      screen.getByText("Create an account to get started"),
    ).toBeInTheDocument();
  });

  it("passes pathname to AuthView", () => {
    renderLogin("sign-in");

    expect(mockAuthView).toHaveBeenCalledWith(
      expect.objectContaining({ pathname: "sign-in" }),
    );
  });

  it("shows error boundary fallback when AuthView crashes", () => {
    mockAuthView.mockImplementation(() => {
      throw new Error("Auth service unavailable");
    });

    renderLogin("sign-in");

    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Auth service unavailable")).toBeInTheDocument();
    expect(screen.getByText("Try again")).toBeInTheDocument();
    // Logo should still be visible outside the error boundary
    expect(screen.getByTestId("mixd-logo")).toBeInTheDocument();
  });
});
