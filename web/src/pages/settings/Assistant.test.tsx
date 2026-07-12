import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { Assistant } from "./Assistant";

function stubStatus(connected: boolean, source: "user" | "server" | null) {
  server.use(
    http.get("*/api/v1/assistant/status", () =>
      HttpResponse.json({ connected, source }),
    ),
  );
}

describe("Assistant settings page", () => {
  it("shows the connect form + Console link when no key is set", async () => {
    stubStatus(false, null);
    renderWithProviders(<Assistant />);

    expect(
      await screen.findByLabelText("Anthropic API key"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Connect" })).toBeInTheDocument();
    const consoleLink = screen.getByRole("link", {
      name: /Anthropic Console/i,
    });
    expect(consoleLink).toHaveAttribute(
      "href",
      expect.stringContaining("console.anthropic.com"),
    );
  });

  it("shows Test + Remove when the user's key is connected", async () => {
    stubStatus(true, "user");
    renderWithProviders(<Assistant />);

    expect(
      await screen.findByRole("button", { name: "Test key" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove" })).toBeInTheDocument();
    // Never renders a key input in the connected state.
    expect(
      screen.queryByLabelText("Anthropic API key"),
    ).not.toBeInTheDocument();
  });

  it("surfaces a rejected key inline without navigating away", async () => {
    stubStatus(false, null);
    server.use(
      http.put("*/api/v1/assistant/key", () =>
        HttpResponse.json(
          {
            error: {
              code: "INVALID_API_KEY",
              message: "Anthropic rejected that key.",
            },
          },
          { status: 400 },
        ),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<Assistant />);

    const input = await screen.findByLabelText("Anthropic API key");
    await user.type(input, "sk-ant-bad000000000000000000");
    await user.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(/rejected/i),
    );
  });
});
