import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { PageLayout } from "./components/layout/PageLayout";
import { Toaster } from "./components/ui/sonner";

const Dashboard = lazy(() =>
  import("./pages/Dashboard").then((m) => ({ default: m.Dashboard })),
);
const Playlists = lazy(() =>
  import("./pages/Playlists").then((m) => ({ default: m.Playlists })),
);
const PlaylistDetail = lazy(() =>
  import("./pages/PlaylistDetail").then((m) => ({ default: m.PlaylistDetail })),
);
const Settings = lazy(() =>
  import("./pages/Settings").then((m) => ({ default: m.Settings })),
);

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<PageLayout />}>
          <Route
            index
            element={
              <Suspense>
                <Dashboard />
              </Suspense>
            }
          />
          <Route
            path="playlists"
            element={
              <Suspense>
                <Playlists />
              </Suspense>
            }
          />
          <Route
            path="playlists/:id"
            element={
              <Suspense>
                <PlaylistDetail />
              </Suspense>
            }
          />
          <Route
            path="settings"
            element={
              <Suspense>
                <Settings />
              </Suspense>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      <Toaster />
    </BrowserRouter>
  );
}
