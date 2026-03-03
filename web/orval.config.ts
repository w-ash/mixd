import { defineConfig } from "orval";

export default defineConfig({
  narada: {
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
          useQuery: true,
          useSuspenseQuery: false,
        },
      },
    },
  },
});
