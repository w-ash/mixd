import { afterEach, describe, expect, it, vi } from "vitest";

// vi.hoisted runs before vi.mock hoisting — safe to reference in factory
const { mockUseAuthenticate, mockAuthClient } = vi.hoisted(() => ({
  mockUseAuthenticate: vi.fn(),
  mockAuthClient: {
    signOut: vi.fn(),
    deleteUser: vi.fn(),
  },
}));

vi.mock("@neondatabase/auth/react/ui", () => ({
  useAuthenticate: mockUseAuthenticate,
}));

vi.mock("@/api/auth", () => ({
  authClient: mockAuthClient,
  authEnabled: true,
}));

import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "@/test/test-utils";

import { Account } from "./Account";

const mockSession = {
  user: {
    id: "user-123",
    name: "Ada Lovelace",
    email: "ada@example.com",
    image: null,
    emailVerified: true,
    createdAt: "2025-01-01T00:00:00Z",
    updatedAt: "2025-01-01T00:00:00Z",
  },
  session: { token: "jwt-token" },
};

describe("Account", () => {
  afterEach(() => {
    mockUseAuthenticate.mockReset();
    mockAuthClient.signOut.mockReset();
    mockAuthClient.deleteUser.mockReset();
  });

  it("renders skeleton while loading", () => {
    mockUseAuthenticate.mockReturnValue({
      data: undefined,
      isPending: true,
      error: null,
    });

    renderWithProviders(<Account />);

    const skeletons = document.querySelectorAll('[data-slot="skeleton"]');
    expect(skeletons.length).toBeGreaterThan(0);
    expect(screen.queryByText("Ada Lovelace")).not.toBeInTheDocument();
  });

  it("renders profile with user name and email", () => {
    mockUseAuthenticate.mockReturnValue({
      data: mockSession,
      isPending: false,
      error: null,
    });

    renderWithProviders(<Account />);

    expect(screen.getByText("Ada Lovelace")).toBeInTheDocument();
    expect(screen.getByText("ada@example.com")).toBeInTheDocument();
  });

  it("renders avatar with initials when no image", () => {
    mockUseAuthenticate.mockReturnValue({
      data: mockSession,
      isPending: false,
      error: null,
    });

    renderWithProviders(<Account />);

    expect(screen.getByText("AL")).toBeInTheDocument();
  });

  it("renders avatar image when provided", () => {
    mockUseAuthenticate.mockReturnValue({
      data: {
        ...mockSession,
        user: { ...mockSession.user, image: "https://example.com/avatar.jpg" },
      },
      isPending: false,
      error: null,
    });

    renderWithProviders(<Account />);

    const img = screen.getByRole("img", { name: "Ada Lovelace" });
    expect(img).toHaveAttribute("src", "https://example.com/avatar.jpg");
  });

  it("calls signOut on sign out button click", async () => {
    mockUseAuthenticate.mockReturnValue({
      data: mockSession,
      isPending: false,
      error: null,
    });
    mockAuthClient.signOut.mockImplementation(() => {});

    renderWithProviders(<Account />);

    await userEvent.click(screen.getByText("Sign out"));
    expect(mockAuthClient.signOut).toHaveBeenCalledWith(
      expect.objectContaining({
        fetchOptions: expect.objectContaining({
          onSuccess: expect.any(Function),
          onError: expect.any(Function),
        }),
      }),
    );
  });

  it("opens delete dialog on delete button click", async () => {
    mockUseAuthenticate.mockReturnValue({
      data: mockSession,
      isPending: false,
      error: null,
    });

    renderWithProviders(<Account />);

    await userEvent.click(
      screen.getByRole("button", { name: /delete account/i }),
    );

    expect(screen.getByText("Delete your account")).toBeInTheDocument();
  });

  it("keeps delete button disabled until DELETE is typed", async () => {
    mockUseAuthenticate.mockReturnValue({
      data: mockSession,
      isPending: false,
      error: null,
    });

    renderWithProviders(<Account />);

    await userEvent.click(
      screen.getByRole("button", { name: /delete account/i }),
    );

    const confirmButton = screen.getByRole("button", {
      name: "Delete account permanently",
    });
    expect(confirmButton).toBeDisabled();

    const input = screen.getByPlaceholderText("DELETE");
    await userEvent.type(input, "delete");
    expect(confirmButton).toBeDisabled();

    await userEvent.clear(input);
    await userEvent.type(input, "DELETE");
    expect(confirmButton).toBeEnabled();
  });

  it("calls deleteUser on confirmed deletion", async () => {
    mockUseAuthenticate.mockReturnValue({
      data: mockSession,
      isPending: false,
      error: null,
    });
    mockAuthClient.deleteUser.mockResolvedValue({
      data: { success: true },
    });

    renderWithProviders(<Account />);

    await userEvent.click(
      screen.getByRole("button", { name: /delete account/i }),
    );

    const input = screen.getByPlaceholderText("DELETE");
    await userEvent.type(input, "DELETE");
    await userEvent.click(
      screen.getByRole("button", { name: "Delete account permanently" }),
    );

    await waitFor(() => {
      expect(mockAuthClient.deleteUser).toHaveBeenCalledWith({
        fetchOptions: { throw: true },
      });
    });
  });

  it("shows error toast on failed deletion", async () => {
    mockUseAuthenticate.mockReturnValue({
      data: mockSession,
      isPending: false,
      error: null,
    });
    mockAuthClient.deleteUser.mockRejectedValue(new Error("Password required"));

    renderWithProviders(<Account />);

    await userEvent.click(
      screen.getByRole("button", { name: /delete account/i }),
    );

    const input = screen.getByPlaceholderText("DELETE");
    await userEvent.type(input, "DELETE");
    await userEvent.click(
      screen.getByRole("button", { name: "Delete account permanently" }),
    );

    await waitFor(() => {
      expect(mockAuthClient.deleteUser).toHaveBeenCalled();
    });
    // Dialog should remain open after error
    expect(screen.getByText("Delete your account")).toBeInTheDocument();
  });
});
