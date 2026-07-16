import { HttpResponse, http } from "msw";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { server } from "#/test/setup";
import {
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { OAuthConsentPage } from "./OAuthConsentPage";

const REQUEST_ID = "0198a5a2-1111-7000-8000-000000000001";

const mockDetails = {
  client_id: "https://client.example/metadata.json",
  client_name: "Claude Code",
  redirect_uri: "http://localhost:53682/callback",
  scopes: [],
  resource: "https://mixd.me/mcp",
};

// jsdom's Location methods are non-configurable, so the whole object is
// swapped for the suite — the page hands the browser to the client's
// callback via location.assign, which is the observable outcome here.
const assign = vi.fn();
const realLocation = window.location;

beforeAll(() => {
  Object.defineProperty(window, "location", {
    value: { ...realLocation, assign },
    writable: true,
    configurable: true,
  });
});

afterAll(() => {
  Object.defineProperty(window, "location", {
    value: realLocation,
    writable: true,
    configurable: true,
  });
});

function renderConsent(requestId: string | null = REQUEST_ID) {
  const search = requestId === null ? "" : `?request_id=${requestId}`;
  renderWithProviders(<OAuthConsentPage />, {
    routerProps: { initialEntries: [`/oauth/consent${search}`] },
  });
  return userEvent.setup();
}

describe("OAuthConsentPage", () => {
  it("renders who is asking and where the code returns to", async () => {
    server.use(
      http.get(`/api/v1/oauth/consent/${REQUEST_ID}`, () =>
        HttpResponse.json(mockDetails),
      ),
    );
    renderConsent();

    expect(await screen.findByText("Claude Code")).toBeInTheDocument();
    expect(
      screen.getByText("http://localhost:53682/callback"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeEnabled();
  });

  it("approve posts the decision and hands the browser to the redirect", async () => {
    assign.mockClear();
    server.use(
      http.get(`/api/v1/oauth/consent/${REQUEST_ID}`, () =>
        HttpResponse.json(mockDetails),
      ),
      http.post(`/api/v1/oauth/consent/${REQUEST_ID}/approve`, () =>
        HttpResponse.json({
          redirect_url: "http://localhost:53682/callback?code=abc&state=s",
        }),
      ),
    );
    const user = renderConsent();

    await user.click(await screen.findByRole("button", { name: "Approve" }));

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(
        "http://localhost:53682/callback?code=abc&state=s",
      ),
    );
  });

  it("deny hands the browser the access_denied redirect", async () => {
    assign.mockClear();
    server.use(
      http.get(`/api/v1/oauth/consent/${REQUEST_ID}`, () =>
        HttpResponse.json(mockDetails),
      ),
      http.post(`/api/v1/oauth/consent/${REQUEST_ID}/deny`, () =>
        HttpResponse.json({
          redirect_url:
            "http://localhost:53682/callback?error=access_denied&state=s",
        }),
      ),
    );
    const user = renderConsent();

    await user.click(await screen.findByRole("button", { name: "Deny" }));

    await waitFor(() =>
      expect(assign).toHaveBeenCalledWith(
        "http://localhost:53682/callback?error=access_denied&state=s",
      ),
    );
  });

  it("shows the expired state when the request is gone", async () => {
    server.use(
      http.get(`/api/v1/oauth/consent/${REQUEST_ID}`, () =>
        HttpResponse.json(
          {
            error: {
              code: "NOT_FOUND",
              message: "This authorization request has expired.",
            },
          },
          { status: 404 },
        ),
      ),
    );
    renderConsent();

    expect(
      await screen.findByText("This authorization request has expired"),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Approve" }),
    ).not.toBeInTheDocument();
  });

  it("explains an incomplete link when request_id is missing", () => {
    renderConsent(null);

    expect(
      screen.getByText(/This consent link is incomplete/),
    ).toBeInTheDocument();
  });
});
