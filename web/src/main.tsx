import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { createQueryClient } from "./api/query-client";
import { ThemeProvider } from "./contexts/ThemeContext";
import "./theme.css";

const root = document.getElementById("root");
if (!root) throw new Error("Root element not found");

createRoot(root).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={createQueryClient()}>
        <App />
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
);
