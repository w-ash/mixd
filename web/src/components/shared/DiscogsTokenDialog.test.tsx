import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { getGetConnectorsApiV1ConnectorsGetQueryKey } from "#/api/generated/connectors/connectors";
import { toasts } from "#/lib/toasts";
import { server } from "#/test/setup";
import {
  createTestQueryClient,
  renderWithProviders,
  screen,
  userEvent,
  waitFor,
} from "#/test/test-utils";

import { DiscogsTokenDialog } from "./DiscogsTokenDialog";

describe("DiscogsTokenDialog", () => {
  it("walks through the personal-access-token steps, not the OAuth app flow", () => {
    renderWithProviders(<DiscogsTokenDialog open onOpenChange={() => {}} />);

    expect(screen.getByText(/Personal access token/)).toBeInTheDocument();
    expect(
      screen.getByText(/ignore the OAuth application fields/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Generate new token/)).toBeInTheDocument();
  });

  it("invalidates the connectors query and closes on success", async () => {
    const user = userEvent.setup();
    const queryClient = createTestQueryClient();
    const invalidateSpy = vi.spyOn(queryClient, "invalidateQueries");
    const onOpenChange = vi.fn();

    renderWithProviders(
      <DiscogsTokenDialog open onOpenChange={onOpenChange} />,
      { queryClient },
    );

    await user.type(
      screen.getByLabelText("Discogs personal access token"),
      "good-token",
    );
    await user.click(screen.getByRole("button", { name: "Connect" }));

    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false);
    });
    expect(invalidateSpy).toHaveBeenCalledWith({
      queryKey: getGetConnectorsApiV1ConnectorsGetQueryKey(),
      refetchType: "active",
    });
  });

  it("surfaces a 400 inline without a global error toast", async () => {
    const user = userEvent.setup();
    const errorToast = vi.spyOn(toasts, "error");
    server.use(
      http.put("*/api/v1/connectors/discogs/token", () => {
        return HttpResponse.json(
          {
            error: {
              // The real backend envelope: middleware maps
              // DiscogsInvalidTokenError to 400 DISCOGS_INVALID_TOKEN.
              code: "DISCOGS_INVALID_TOKEN",
              message: "Discogs rejected the token",
            },
          },
          { status: 400 },
        );
      }),
    );

    renderWithProviders(<DiscogsTokenDialog open onOpenChange={() => {}} />);

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
    renderWithProviders(<DiscogsTokenDialog open onOpenChange={() => {}} />);

    expect(screen.getByRole("button", { name: "Connect" })).toBeDisabled();
  });
});
