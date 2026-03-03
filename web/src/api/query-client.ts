import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: (failureCount, error) => {
          if (error instanceof ApiError) {
            return error.status >= 500 && failureCount < 2;
          }
          return false;
        },
      },
    },
  });
}
