import { fireEvent } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "#/stores/chat-store";
import { server } from "#/test/setup";
import {
  mockMatchMedia,
  renderWithProviders,
  screen,
  waitFor,
} from "#/test/test-utils";

import { PageLayout } from "./PageLayout";

function stubChatAvailable(connected: boolean) {
  server.use(
    http.get("*/api/v1/assistant/status", () =>
      HttpResponse.json({ connected, source: connected ? "user" : null }),
    ),
  );
}

describe("PageLayout", () => {
  beforeEach(() => {
    useChatStore.setState({ isPanelOpen: false, messages: [] });
    // Default: assistant connected so the panel surface renders. The gate
    // itself is covered in the "gated off" block below.
    stubChatAvailable(true);
  });

  it("renders the desktop shell at-or-above lg breakpoint", () => {
    mockMatchMedia(1280);
    renderWithProviders(<PageLayout />);

    expect(
      screen.getByRole("navigation", { name: /main navigation/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: /mobile navigation/i }),
    ).not.toBeInTheDocument();
  });

  it("renders the mobile shell below lg breakpoint", () => {
    mockMatchMedia(390);
    renderWithProviders(<PageLayout />);

    expect(
      screen.getByRole("navigation", { name: /mobile navigation/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("navigation", { name: /main navigation/i }),
    ).not.toBeInTheDocument();
  });

  it("treats iPad portrait (820px) as mobile", () => {
    mockMatchMedia(820);
    renderWithProviders(<PageLayout />);

    expect(
      screen.getByRole("navigation", { name: /mobile navigation/i }),
    ).toBeInTheDocument();
  });

  describe("chat assistant panel (desktop)", () => {
    it("shows the edge tab when collapsed and opens the panel on click", async () => {
      mockMatchMedia(1280);
      renderWithProviders(<PageLayout />);

      const tab = await screen.findByRole("button", {
        name: /open chat assistant/i,
      });
      expect(tab).toBeInTheDocument();

      fireEvent.click(tab);

      expect(
        await screen.findByRole("button", { name: /close chat/i }),
      ).toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: /open chat assistant/i }),
      ).not.toBeInTheDocument();
    });

    it("toggles the panel with Cmd+K", async () => {
      mockMatchMedia(1280);
      renderWithProviders(<PageLayout />);

      // Wait for the per-user availability gate to resolve before toggling.
      expect(
        await screen.findByRole("button", { name: /open chat assistant/i }),
      ).toBeInTheDocument();

      fireEvent.keyDown(window, { key: "k", metaKey: true });
      expect(
        await screen.findByRole("button", { name: /close chat/i }),
      ).toBeInTheDocument();

      fireEvent.keyDown(window, { key: "k", metaKey: true });
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: /open chat assistant/i }),
        ).toBeInTheDocument(),
      );
    });

    it("closes the open panel on Escape", async () => {
      mockMatchMedia(1280);
      renderWithProviders(<PageLayout />);

      await screen.findByRole("button", { name: /open chat assistant/i });
      fireEvent.keyDown(window, { key: "k", metaKey: true });
      await screen.findByRole("button", { name: /close chat/i });

      fireEvent.keyDown(window, { key: "Escape" });
      await waitFor(() =>
        expect(
          screen.getByRole("button", { name: /open chat assistant/i }),
        ).toBeInTheDocument(),
      );
    });

    it("gated off: no edge tab and Cmd+K is inert without a key", async () => {
      stubChatAvailable(false);
      mockMatchMedia(1280);
      renderWithProviders(<PageLayout />);

      // Main shell renders; the assistant surface never appears.
      await screen.findByRole("navigation", { name: /main navigation/i });
      fireEvent.keyDown(window, { key: "k", metaKey: true });

      await waitFor(() =>
        expect(
          screen.queryByRole("button", { name: /open chat assistant/i }),
        ).not.toBeInTheDocument(),
      );
      expect(
        screen.queryByRole("button", { name: /close chat/i }),
      ).not.toBeInTheDocument();
    });
  });
});
