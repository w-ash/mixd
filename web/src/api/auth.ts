/**
 * Neon Auth client — opt-in via VITE_NEON_AUTH_URL env var.
 *
 * When the env var is empty (local dev), `authEnabled` is false and
 * no auth provider is mounted. The app behaves exactly as before.
 *
 * JWT retrieval uses `authClient.token()` (the Neon Auth JWT plugin)
 * to get Bearer tokens for API requests. Session cookies live on the
 * auth service domain and are never sent to our backend.
 * See: https://neon.com/docs/auth/guides/plugins/jwt
 */
import { createAuthClient } from "@neondatabase/auth";

export const NEON_AUTH_URL = import.meta.env.VITE_NEON_AUTH_URL as
  | string
  | undefined;

export const authEnabled = Boolean(NEON_AUTH_URL);

// biome-ignore lint/style/noNonNullAssertion: guarded by authEnabled check
export const authClient = authEnabled ? createAuthClient(NEON_AUTH_URL!) : null;
