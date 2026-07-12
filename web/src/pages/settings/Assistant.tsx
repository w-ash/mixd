import { CheckCircle2, ExternalLink, Sparkles } from "lucide-react";
import { useState } from "react";

import { ApiError } from "#/api/client";
import { PageHeader } from "#/components/layout/PageHeader";
import { SectionHeader } from "#/components/shared/SectionHeader";
import { Button } from "#/components/ui/button";
import { Input } from "#/components/ui/input";
import { Skeleton } from "#/components/ui/skeleton";
import { useAssistantKey } from "#/hooks/useAssistantKey";
import { useChatAvailable } from "#/hooks/useChatAvailable";
import { toasts } from "#/lib/toasts";

const CONSOLE_URL = "https://console.anthropic.com/settings/keys";

// Clean, modern surface: a barely-perceptible tonal gradient between the two
// warm surface tokens (no color gradient — flat, not skeuomorphic), a hairline
// border, and the system's silky soft drop shadow.
const cardClass =
  "rounded-2xl border border-border/60 bg-gradient-to-b from-surface-elevated to-surface p-6 shadow-elevated";

function connectErrorMessage(error: unknown): string | null {
  if (error instanceof ApiError) return error.message;
  if (error) return "Something went wrong. Please try again.";
  return null;
}

function NotConnected() {
  const { connect, isConnecting, connectError } = useAssistantKey();
  const [apiKey, setApiKey] = useState("");
  const message = connectErrorMessage(connectError);

  async function onConnect(e: React.FormEvent) {
    e.preventDefault();
    const key = apiKey.trim();
    if (!key) return;
    try {
      await connect(key);
      setApiKey("");
    } catch {
      // Error is surfaced inline via connectError.
    }
  }

  return (
    <div className="space-y-3">
      <SectionHeader
        title="Connect the assistant"
        description="The AI assistant runs on your own Anthropic API key, so your usage and spend stay yours."
      />
      <div className={`space-y-5 ${cardClass}`}>
        <ol className="list-decimal space-y-1.5 pl-5 text-sm text-text-muted">
          <li>
            Create a key in the{" "}
            <a
              href={CONSOLE_URL}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
            >
              Anthropic Console <ExternalLink className="size-3" />
            </a>
            .
          </li>
          <li>
            Add a payment method under Billing first — a key without billing is
            rejected on use.
          </li>
          <li>Paste the key (starts with "sk-ant-") below.</li>
        </ol>

        <form onSubmit={onConnect} className="space-y-2">
          <Input
            type="password"
            autoComplete="off"
            placeholder="sk-ant-..."
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            aria-label="Anthropic API key"
            aria-invalid={message ? true : undefined}
          />
          {message && (
            <p role="alert" className="text-sm text-destructive">
              {message}
            </p>
          )}
          <Button type="submit" disabled={!apiKey.trim() || isConnecting}>
            {isConnecting ? "Validating..." : "Connect"}
          </Button>
        </form>
      </div>
    </div>
  );
}

function Connected({ source }: { source: "user" | "server" }) {
  const { remove, isRemoving, test, isTesting } = useAssistantKey();

  async function onTest() {
    const result = await test();
    const ok = result.status === 200 && result.data.ok;
    if (ok) toasts.success("Key is valid");
    else toasts.message("Anthropic rejected the key");
  }

  return (
    <div className="space-y-3">
      <SectionHeader
        title="AI assistant"
        description="Manage the Anthropic credential powering your assistant."
      />
      <div className={`space-y-5 ${cardClass}`}>
        <div className="flex items-center gap-3">
          <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-status-success/10">
            <CheckCircle2 className="size-5 text-status-success" />
          </span>
          <div className="space-y-0.5">
            <p className="font-display text-sm">
              {source === "user" ? "Connected" : "Using the server key"}
            </p>
            <p className="text-sm text-text-muted">
              {source === "user"
                ? "Your assistant is ready. Your key is stored encrypted and never shown again."
                : "This deployment provides a shared fallback key. Add your own to use your own account."}
            </p>
          </div>
        </div>

        {source === "user" && (
          <div className="flex gap-2">
            <Button variant="secondary" onClick={onTest} disabled={isTesting}>
              {isTesting ? "Testing..." : "Test key"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => remove()}
              disabled={isRemoving}
            >
              {isRemoving ? "Removing..." : "Remove"}
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

export function Assistant() {
  const { available, source, isLoading } = useChatAvailable();

  return (
    <div>
      <title>Assistant — Mixd</title>
      <PageHeader
        title="Assistant"
        description="Connect an AI assistant to build workflows and manage your library in plain language."
      />
      <div className="mb-6 flex items-center gap-2 text-sm text-text-muted">
        <Sparkles className="size-4 text-primary" />
        Powered by Anthropic's Claude — bring your own API key.
      </div>

      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-3 w-32" />
          <Skeleton className="h-40 w-full rounded-2xl" />
        </div>
      ) : available && source ? (
        <Connected source={source} />
      ) : (
        <NotConnected />
      )}
    </div>
  );
}
