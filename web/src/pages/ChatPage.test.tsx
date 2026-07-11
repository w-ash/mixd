import { beforeEach, describe, expect, it, vi } from "vitest";

import { sendChatMessage } from "#/api/chat-sse";
import { useChatStore } from "#/stores/chat-store";
import { mockMatchMedia, renderWithProviders, screen } from "#/test/test-utils";

vi.mock("#/api/chat-sse", () => ({ sendChatMessage: vi.fn() }));
vi.mocked(sendChatMessage).mockResolvedValue(undefined);

// useNavigate is mocked so the desktop redirect can be asserted; everything
// else in react-router (MemoryRouter) stays real.
const { mockNavigate } = vi.hoisted(() => ({ mockNavigate: vi.fn() }));
vi.mock("react-router", async (importActual) => {
  const actual = await importActual<typeof import("react-router")>();
  return { ...actual, useNavigate: () => mockNavigate };
});

import { ChatPage } from "./ChatPage";

describe("ChatPage", () => {
  beforeEach(() => {
    mockNavigate.mockReset();
    useChatStore.setState({ isPanelOpen: false, messages: [] });
  });

  it("renders the full-screen chat panel on mobile", () => {
    mockMatchMedia(390);
    renderWithProviders(<ChatPage />);

    expect(
      screen.getByPlaceholderText(/ask about your music/i),
    ).toBeInTheDocument();
    // fullScreen hides the panel's own header/close affordance.
    expect(
      screen.queryByRole("button", { name: /close chat/i }),
    ).not.toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(useChatStore.getState().isPanelOpen).toBe(false);
  });

  it("opens the side panel and redirects home on desktop", () => {
    mockMatchMedia(1280);
    renderWithProviders(<ChatPage />);

    expect(useChatStore.getState().isPanelOpen).toBe(true);
    expect(mockNavigate).toHaveBeenCalledWith("/", { replace: true });
    expect(
      screen.queryByPlaceholderText(/ask about your music/i),
    ).not.toBeInTheDocument();
  });
});
