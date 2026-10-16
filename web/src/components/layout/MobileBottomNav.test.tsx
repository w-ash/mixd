import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { useGetAssistantStatusApiV1AssistantStatusGet } from "#/api/generated/assistant/assistant";
import { server } from "#/test/setup";
import {
  mockMatchMedia,
  renderWithProviders,
  screen,
  userEvent,
} from "#/test/test-utils";

import { MobileBottomNav } from "./MobileBottomNav";

function stubChat(connected: boolean) {
  server.use(
    http.get("*/api/v1/assistant/status", () =>
      HttpResponse.json({ connected, source: connected ? "user" : null }),
    ),
  );
}

/** Renders "status settled" once the assistant-status query has resolved. */
function StatusSettledProbe() {
  const { isSuccess } = useGetAssistantStatusApiV1AssistantStatusGet();
  return isSuccess ? <span>status settled</span> : null;
}

describe("MobileBottomNav", () => {
  it("renders the four primary tabs", () => {
    mockMatchMedia(390);
    renderWithProviders(<MobileBottomNav />);

    expect(screen.getByRole("link", { name: /home/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /library/i })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /workflows/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /more/i })).toBeInTheDocument();
  });

  it("exposes the navigation landmark", () => {
    renderWithProviders(<MobileBottomNav />);
    expect(
      screen.getByRole("navigation", { name: /mobile navigation/i }),
    ).toBeInTheDocument();
  });

  it("highlights the active route", () => {
    renderWithProviders(<MobileBottomNav />, {
      routerProps: { initialEntries: ["/library"] },
    });

    const libraryLink = screen.getByRole("link", { name: /library/i });
    expect(libraryLink.className).toContain("text-primary");
  });

  it("opens the more sheet when the More button is tapped", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MobileBottomNav />);

    await user.click(screen.getByRole("button", { name: /more/i }));

    expect(
      screen.getByRole("link", { name: /playlists/i }),
    ).toBeInTheDocument();
  });

  it("hides the Ask tab when the assistant is unavailable", async () => {
    stubChat(false);
    renderWithProviders(
      <>
        <MobileBottomNav />
        <StatusSettledProbe />
      </>,
    );

    // Assert only after the status answered, or the check passes while loading.
    await screen.findByText("status settled");
    expect(screen.getByText("Home")).toBeInTheDocument();
    expect(screen.queryByText("Ask")).not.toBeInTheDocument();
  });

  it("shows the Ask tab once a key is connected", async () => {
    stubChat(true);
    renderWithProviders(<MobileBottomNav />);

    expect(await screen.findByText("Ask")).toBeInTheDocument();
  });
});
