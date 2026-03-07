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
const Imports = lazy(() =>
  import("./pages/Imports").then((m) => ({ default: m.Imports })),
);
const Library = lazy(() =>
  import("./pages/Library").then((m) => ({ default: m.Library })),
);
const TrackDetail = lazy(() =>
  import("./pages/TrackDetail").then((m) => ({ default: m.TrackDetail })),
);
const Settings = lazy(() =>
  import("./pages/Settings").then((m) => ({ default: m.Settings })),
);
const Workflows = lazy(() =>
  import("./pages/Workflows").then((m) => ({ default: m.Workflows })),
);
const WorkflowDetail = lazy(() =>
  import("./pages/WorkflowDetail").then((m) => ({
    default: m.WorkflowDetail,
  })),
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
            path="imports"
            element={
              <Suspense>
                <Imports />
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
            path="workflows"
            element={
              <Suspense>
                <Workflows />
              </Suspense>
            }
          />
          <Route
            path="workflows/:id"
            element={
              <Suspense>
                <WorkflowDetail />
              </Suspense>
            }
          />
          <Route
            path="library"
            element={
              <Suspense>
                <Library />
              </Suspense>
            }
          />
          <Route
            path="library/:id"
            element={
              <Suspense>
                <TrackDetail />
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
