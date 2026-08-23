import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { ApiError } from "#/api/client";
import { Button } from "#/components/ui/button";
import {
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { Input } from "#/components/ui/input";
import { ResponsiveDialog } from "#/components/ui/responsive-dialog";
import { useDiscogsToken } from "#/hooks/useDiscogsToken";

const DEVELOPERS_URL = "https://www.discogs.com/settings/developers";

function connectErrorMessage(error: unknown): string | null {
  if (error instanceof ApiError) return error.message;
  if (error) return "Something went wrong. Please try again.";
  return null;
}

interface DiscogsTokenDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * The token-connect form for Discogs (`auth_method: "token"`, v0.11.1).
 *
 * Opened from the connector card's Connect button in place of the OAuth
 * redirect — Discogs uses a BYO personal access token (never expires,
 * validated live, stored encrypted, never echoed back). The steps below
 * deliberately steer past the OAuth application fields on the Discogs
 * developer page, which are not what we need.
 */
export function DiscogsTokenDialog({
  open,
  onOpenChange,
}: DiscogsTokenDialogProps) {
  const { connect, isConnecting, connectError, resetConnect } =
    useDiscogsToken();
  const [token, setToken] = useState("");
  const message = connectErrorMessage(connectError);

  function handleOpenChange(next: boolean) {
    if (!next) {
      // The token value must not survive a closed dialog.
      setToken("");
      resetConnect();
    }
    onOpenChange(next);
  }

  async function onConnect(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = token.trim();
    if (!trimmed) return;
    try {
      await connect(trimmed);
      setToken("");
      onOpenChange(false);
    } catch {
      // Error is surfaced inline via connectError.
    }
  }

  return (
    <ResponsiveDialog open={open} onOpenChange={handleOpenChange}>
      <DialogHeader>
        <DialogTitle>Connect Discogs</DialogTitle>
        <DialogDescription>
          Discogs connects with a personal access token — your collection stays
          readable even while Mixd has no Discogs app of its own.
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={onConnect} className="mt-4 space-y-4">
        <ol className="list-decimal space-y-1.5 pl-5 text-sm text-text-muted">
          <li>
            On discogs.com, open{" "}
            <a
              href={DEVELOPERS_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
            >
              Settings → Developers <ExternalLink className="size-3" />
            </a>
            .
          </li>
          <li>
            In the <strong>Personal access token</strong> section (ignore the
            OAuth application fields), choose Generate new token.
          </li>
          <li>Paste the token below.</li>
        </ol>

        <div className="space-y-2">
          <Input
            type="password"
            autoComplete="off"
            placeholder="Your Discogs token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            aria-label="Discogs personal access token"
            aria-invalid={message ? true : undefined}
          />
          {message && (
            <p role="alert" className="text-sm text-destructive">
              {message}
            </p>
          )}
        </div>

        <DialogFooter className="mt-6">
          <Button
            type="button"
            variant="outline"
            onClick={() => handleOpenChange(false)}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={!token.trim() || isConnecting}>
            {isConnecting ? "Validating..." : "Connect"}
          </Button>
        </DialogFooter>
      </form>
    </ResponsiveDialog>
  );
}
