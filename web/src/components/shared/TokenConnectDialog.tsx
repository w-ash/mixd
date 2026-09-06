import { ExternalLink } from "lucide-react";
import { useState } from "react";

import { Button } from "#/components/ui/button";
import {
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "#/components/ui/dialog";
import { Input } from "#/components/ui/input";
import { ResponsiveDialog } from "#/components/ui/responsive-dialog";
import { useTokenConnect } from "#/hooks/useTokenConnect";
import { type TokenConnectStep, tokenConnectCopyFor } from "#/lib/connectors";
import { connectErrorMessage } from "#/lib/toasts";

/** One instruction line, assembled from the markup-free copy table. */
function Step({ step }: { step: TokenConnectStep }) {
  return (
    <li>
      {step.text}
      {step.emphasis && <strong>{step.emphasis}</strong>}
      {step.link && (
        <a
          href={step.link.url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-primary hover:underline"
        >
          {step.link.label} <ExternalLink className="size-3" />
        </a>
      )}
      {step.tail}
    </li>
  );
}

interface TokenConnectDialogProps {
  /** Connector registry key — the `{service}` path segment of the PUT. */
  service: string;
  /** Connector display name, used for every piece of generic copy. */
  displayName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * The connect form for any `auth_method: "token"` connector.
 *
 * Opened from the connector card's Connect button in place of the OAuth
 * redirect: these connectors take a BYO personal access token, validated
 * live, stored encrypted, never echoed back. Title and copy come from
 * `display_name`; the provider-specific "where to get a token" steps come
 * from the table in `lib/connectors.ts`, so a second token connector is a
 * table entry rather than a branch here.
 */
export function TokenConnectDialog({
  service,
  displayName,
  open,
  onOpenChange,
}: TokenConnectDialogProps) {
  const { connect, isConnecting, connectError, resetConnect } = useTokenConnect(
    service,
    displayName,
  );
  const [token, setToken] = useState("");
  const copy = tokenConnectCopyFor(service);
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
        <DialogTitle>Connect {displayName}</DialogTitle>
        <DialogDescription>
          {copy?.rationale ??
            `${displayName} connects with a personal access token you generate yourself.`}
        </DialogDescription>
      </DialogHeader>

      <form onSubmit={onConnect} className="mt-4 space-y-4">
        {copy && (
          <ol className="list-decimal space-y-1.5 pl-5 text-sm text-text-muted">
            {copy.steps.map((step) => (
              <Step key={step.text} step={step} />
            ))}
          </ol>
        )}

        <div className="space-y-2">
          <Input
            type="password"
            autoComplete="off"
            placeholder={`Your ${displayName} token`}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            aria-label={`${displayName} personal access token`}
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
