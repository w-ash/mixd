/**
 * Composable toast layer.
 *
 * Components call `toasts.success` / `toasts.error` / `toasts.promise` for
 * explicit notifications. Mutations should prefer declaring
 * `meta: { errorLabel }` on the mutation — the global `MutationCache.onError`
 * handler formats and shows the toast automatically.
 *
 * `formatApiError` maps known `ApiError.code` values to friendly titles so
 * infrastructure errors (rate limit, database unavailable, …) render
 * consistently everywhere they surface.
 */

import type { Mutation } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { toast } from "sonner";

import { ApiError } from "#/api/client";

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastOptions {
  description?: ReactNode;
  id?: string | number;
  action?: ToastAction;
  duration?: number;
}

/** Human-readable `{ title, description }` for any thrown error. */
export function formatApiError(err: unknown): {
  title: string;
  description?: string;
} {
  if (err instanceof ApiError) {
    switch (err.code) {
      case "DATABASE_UNAVAILABLE":
        return {
          title: "Database unavailable",
          description: "We'll retry automatically. Your changes aren't lost.",
        };
      case "CONNECTOR_NOT_AVAILABLE":
        return {
          title: "Service unavailable",
          description: err.message,
        };
      case "VALIDATION_ERROR":
      case "VALIDATION_FAILED":
        return { title: "Invalid input", description: err.message };
      case "NOT_FOUND":
        return { title: "Not found", description: err.message };
      case "UNAUTHORIZED":
        return { title: "Signed out", description: "Please sign in again." };
      case "FORBIDDEN":
        return {
          title: "Not allowed",
          description: err.message,
        };
      case "RATE_LIMITED":
        return {
          title: "Too many requests",
          description: "Please try again in a moment.",
        };
      default:
        return { title: err.message };
    }
  }
  if (err instanceof Error) return { title: err.message };
  return { title: "Something went wrong" };
}

export const toasts = {
  success(title: string, options: ToastOptions = {}) {
    toast.success(title, options);
  },

  /** Formats an arbitrary error into description; shows under `title`. */
  error(title: string, error: unknown, options: ToastOptions = {}) {
    const { description } = formatApiError(error);
    toast.error(title, { description, ...options });
  },

  /** Plain error without a thrown exception (UX messages, validation, etc.). */
  message(title: string, options: ToastOptions = {}) {
    toast.error(title, options);
  },

  info(title: string, options: ToastOptions = {}) {
    toast.info(title, options);
  },

  /**
   * Sonner's promise toast — loading → success / error in one call.
   * Use for long-running operations where the pending state matters
   * (syncs, imports, multi-step flows).
   */
  promise<T>(
    promise: Promise<T>,
    messages: {
      loading: string;
      success: string | ((result: T) => string);
      error?: string | ((err: unknown) => string);
    },
  ) {
    return toast.promise(promise, {
      loading: messages.loading,
      success: messages.success,
      error: messages.error ?? ((err) => formatApiError(err).title),
    });
  },
};

/**
 * `MutationCache.onError` handler.
 *
 * Reads two optional `meta` fields on the mutation:
 *   - `errorLabel`: string — the toast title. Falls back to a generic message.
 *   - `suppressErrorToast`: true — skip the global toast when the caller
 *     already shows an inline error (e.g., form-level field errors).
 */
export function createMutationErrorHandler() {
  return (
    error: unknown,
    _variables: unknown,
    _context: unknown,
    mutation: Mutation<unknown, unknown, unknown, unknown>,
  ) => {
    const meta = mutation.meta;
    if (meta?.suppressErrorToast) return;
    const label =
      typeof meta?.errorLabel === "string"
        ? meta.errorLabel
        : "Something went wrong";
    toasts.error(label, error);
  };
}
