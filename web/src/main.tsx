import { NeonAuthUIProvider } from "@neondatabase/auth/react/ui";
import "@neondatabase/auth/ui/css";
import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { authClient, authEnabled } from "./api/auth";
import { createQueryClient } from "./api/query-client";
import { ThemeProvider } from "./contexts/ThemeContext";
import "./theme.css";

const root = document.getElementById("root");
if (!root) throw new Error("Root element not found");

const queryClient = createQueryClient();

function Root() {
  const app = (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ThemeProvider>
  );

  if (!authEnabled || !authClient) return app;

  return (
    <NeonAuthUIProvider authClient={authClient} defaultTheme="dark">
      {app}
    </NeonAuthUIProvider>
  );
}

createRoot(root).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
