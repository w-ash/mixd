import { defineConfig } from "orval";

export default defineConfig({
  mixd: {
    input: {
      target: "./openapi.json",
    },
    output: {
      mode: "tags-split",
      target: "src/api/generated",
      schemas: "src/api/generated/model",
      client: "react-query",
      mock: true,
      override: {
        mutator: {
          path: "src/api/client.ts",
          name: "customFetch",
        },
        query: {
          // Defaults: GET -> useQuery, non-GET -> useMutation. (orval 8.15
          // changed precedence so a global `useQuery: true` would force POST/
          // PUT/DELETE into query hooks and suppress mutations — so don't set it.)
          useSuspenseQuery: false,
          // Stamps every generated mutation with the cache tags its route
          // owns, so invalidation is declared once here instead of hand-rolled
          // at ~40 callsites. See src/api/cache-tags.ts.
          mutationOptions: {
            path: "src/api/cache-tags-mutator.ts",
            name: "withCacheTags",
          },
        },
      },
    },
  },
});
