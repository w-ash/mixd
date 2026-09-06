import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { getGetConnectorsApiV1ConnectorsGetQueryKey } from "#/api/generated/connectors/connectors";
import { toasts } from "#/lib/toasts";
import { seedQuery, wasInvalidated } from "#/test/query-utils";
import { server } from "#/test/setup";
import {
  createTestQueryClient,
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";
import { TokenConnectDialog } from "./TokenConnectDialog";

/** The dialog under its only shipped connector. */
function DiscogsDialog(props: { onOpenChange?: (open: boolean) => void }) {
  return (
    <TokenConnectDialog
      service="discogs"
      displayName="Discogs"
      open
      onOpenChange={props.onOpenChange ?? (() => {})}
    />
  );
}

describe("TokenConnectDialog", () => {
  it("walks through the personal-access-token steps, not the OAuth app flow", () => {
    renderWithProviders(<DiscogsDialog />);

    expect(screen.getByText("Connect Discogs")).toBeInTheDocument();
    expect(screen.getByText(/Personal access token/)).toBeInTheDocument();
    expect(
      screen.getByText(/ignore the OAuth application fields/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Generate new token/)).toBeInTheDocument();
  });

  it("invalidates the connectors query and closes on success", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    seedQuery(queryClient, getGetConnectorsApiV1ConnectorsGetQueryKey());
    const onOpenChange = vi.fn();

    renderWithProviders(<DiscogsDialog onOpenChange={onOpenChange} />, {
      queryClient,
    });

    await user.type(
      screen.getByLabelText("Discogs personal access token"),
      "good-token",
    );
    await user.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
    expect(
      wasInvalidated(queryClient, getGetConnectorsApiV1ConnectorsGetQueryKey()),
    ).toBe(true);
  });

  it("surfaces a 400 inline without a global error toast", async () => {
    const user = userEvent.setup();
    const errorToast = vi.spyOn(toasts, "error");
    server.use(
      http.put("*/api/v1/connectors/discogs/token", () => {
        return HttpResponse.json(
          {
            error: {
              // The real backend envelope: middleware maps the connector's
              // invalid-token error to a 400.
              code: "DISCOGS_INVALID_TOKEN",
              message: "Discogs rejected the token",
            },
          },
          { status: 400 },
        );
      }),
    );

    renderWithProviders(<DiscogsDialog />);

    await user.type(
      screen.getByLabelText("Discogs personal access token"),
      "bad-token",
    );
    await user.click(screen.getByRole("button", { name: "Connect" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Discogs rejected the token");
    expect(errorToast).not.toHaveBeenCalled();
  });

  it("disables submit while the token is empty", () => {
    renderWithProviders(<DiscogsDialog />);

    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
  });

  it("PUTs to the connector named in props, with generic copy", async () => {
    const user = userEvent.setup();
    let putUrl = "";
    server.use(
      http.put("*/api/v1/connectors/:service/token", ({ request }) => {
        putUrl = new URL(request.url).pathname;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    // A token connector this build ships no instructions for: title, copy
    // and field label still come from `display_name`, and the form works.
    renderWithProviders(
      <TokenConnectDialog
        service="future_crate"
        displayName="Future Crate"
        open
        onOpenChange={() => {}}
      />,
    );

    expect(screen.getByText("Connect Future Crate")).toBeInTheDocument();
    expect(screen.queryByRole("list")).not.toBeInTheDocument();

    await user.type(
      screen.getByLabelText("Future Crate personal access token"),
      "some-token",
    );
    await user.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => {
      expect(putUrl).toBe("/api/v1/connectors/future_crate/token");
    });
  });
});
