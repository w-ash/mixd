/**
 * Custom fetch wrapper for Orval-generated hooks.
 *
 * Orval v8 calls this with a standard fetch-compatible signature:
 *   customFetch<T>(url: string, init: RequestInit): Promise<T>
 *
 * Intercepts non-OK responses, parses the error envelope,
 * and throws a typed ApiError. Base URL is empty since
 * Vite's proxy handles routing to the backend.
 *
 * Attaches a Bearer token from Neon Auth when auth is enabled.
 */

import { authClient, authEnabled } from "./auth";

/** Known backend error codes used for retry/display decisions. */
export const API_ERROR_CODES = {
  DATABASE_UNAVAILABLE: "DATABASE_UNAVAILABLE",
  CONNECTOR_NOT_AVAILABLE: "CONNECTOR_NOT_AVAILABLE",
} as const;

export class ApiError extends Error {
  status: number;
  code: string;
  details?: Record<string, string>;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: Record<string, string>,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export async function customFetch<T>(
  url: string,
  init: RequestInit = {},
): Promise<T> {
  if (authClient) {
    const { data } = await authClient.token();
    if (data?.token) {
      const headers = new Headers(init.headers);
      headers.set("Authorization", `Bearer ${data.token}`);
      init = { ...init, headers };
    }
  }

  const response = await fetch(url, init);

  // 204 No Content — return envelope with undefined data
  if (response.status === 204) {
    return { data: undefined, status: 204, headers: response.headers } as T;
  }

  const body = await response.json();

  if (!response.ok) {
    // Session expired or invalid — redirect to sign-in with error context
    if (authEnabled && response.status === 401) {
      // Avoid redirect loop if already on an auth page
      if (!window.location.pathname.startsWith("/auth/")) {
        window.location.href = "/auth/sign-in?error=session_expired";
        return new Promise(() => {});
      }
    }

    const error = body?.error;
    throw new ApiError(
      response.status,
      error?.code ?? "UNKNOWN_ERROR",
      error?.message ?? "An unknown error occurred",
      error?.details,
    );
  }

  // Orval v8 expects {data, status, headers} discriminated union envelope
  return {
    data: body,
    status: response.status,
    headers: response.headers,
  } as T;
}
