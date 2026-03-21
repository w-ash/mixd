/**
 * Neon Auth client — opt-in via VITE_NEON_AUTH_URL env var.
 *
 * When the env var is empty (local dev), `authEnabled` is false and
 * no auth provider is mounted. The app behaves exactly as before.
 */
import { createAuthClient } from "@neondatabase/auth";

export const NEON_AUTH_URL = import.meta.env.VITE_NEON_AUTH_URL as
  | string
  | undefined;

export const authEnabled = Boolean(NEON_AUTH_URL);

export const authClient = authEnabled ? createAuthClient(NEON_AUTH_URL!) : null;
