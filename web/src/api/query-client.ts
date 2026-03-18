import { QueryClient } from "@tanstack/react-query";

import { API_ERROR_CODES, ApiError } from "./client";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (failureCount, error) => {
          if (error instanceof ApiError) {
            // Don't retry infrastructure errors — they won't self-resolve
            if (
              error.code === API_ERROR_CODES.DATABASE_UNAVAILABLE ||
              error.code === API_ERROR_CODES.CONNECTOR_NOT_AVAILABLE
            ) {
              return false;
            }
            return error.status >= 500 && failureCount < 2;
          }
          return false;
        },
      },
    },
  });
}
