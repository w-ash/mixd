import { delay, HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { sendChatMessage } from "#/api/chat-sse";
import { useChatStore } from "#/stores/chat-store";
import { server } from "#/test/setup";
import {
  mockMatchMedia,
  renderWithProviders,
  screen,
  waitFor,
} from "#/test/test-utils";

vi.mock("#/api/chat-sse", () => ({ sendChatMessage: vi.fn() }));
vi.mocked(sendChatMessage).mockResolvedValue(undefined);

function stubChatAvailable(connected: boolean) {
  server.use(
    http.get("*/api/v1/assistant/status", () =>
      HttpResponse.json({ connected, source: connected ? "user" : null }),
    ),
  );
}

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

  it("renders the full-screen chat panel on mobile when connected", async () => {
    stubChatAvailable(true);
    mockMatchMedia(390);
    renderWithProviders(<ChatPage />);

    expect(
      await screen.findByPlaceholderText(/ask about your music/i),
    ).toBeInTheDocument();
    // fullScreen hides the panel's own header/close affordance.
    expect(
      screen.queryByRole("button", { name: /close chat/i }),
    ).not.toBeInTheDocument();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(useChatStore.getState().isPanelOpen).toBe(false);
  });

  it("redirects to settings on mobile when no key is connected", async () => {
    stubChatAvailable(false);
    mockMatchMedia(390);
    renderWithProviders(<ChatPage />);

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith("/settings/assistant", {
        replace: true,
      }),
    );
    expect(
      screen.queryByPlaceholderText(/ask about your music/i),
    ).not.toBeInTheDocument();
  });

  it("opens the side panel and redirects home on desktop", async () => {
    stubChatAvailable(true);
    mockMatchMedia(1280);
    renderWithProviders(<ChatPage />);

    await waitFor(() => expect(useChatStore.getState().isPanelOpen).toBe(true));
    expect(mockNavigate).toHaveBeenCalledWith("/", { replace: true });
    expect(
      screen.queryByPlaceholderText(/ask about your music/i),
    ).not.toBeInTheDocument();
  });

  it("waits for the availability gate before redirecting on desktop", async () => {
    // Gate never resolves — a deep-link/refresh must NOT redirect home while
    // it's still loading, or the page unmounts and the panel never opens.
    server.use(
      http.get("*/api/v1/assistant/status", async () => {
        await delay("infinite");
        return HttpResponse.json({ connected: true, source: "user" });
      }),
    );
    mockMatchMedia(1280);
    renderWithProviders(<ChatPage />);

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(useChatStore.getState().isPanelOpen).toBe(false);
  });
});
