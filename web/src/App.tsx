import { lazy, Suspense } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";

import { PageLayout } from "./components/layout/PageLayout";
import { Skeleton } from "./components/ui/skeleton";
import { Toaster } from "./components/ui/sonner";

function PageSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-80" />
      </div>
      <Skeleton className="h-64 w-full rounded-lg" />
    </div>
  );
}

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
const WorkflowRunDetail = lazy(() =>
  import("./pages/WorkflowRunDetail").then((m) => ({
    default: m.WorkflowRunDetail,
  })),
);
const WorkflowEditor = lazy(() => import("./pages/WorkflowEditor"));

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<PageLayout />}>
          <Route
            index
            element={
              <Suspense fallback={<PageSkeleton />}>
                <Dashboard />
              </Suspense>
            }
          />
          <Route
            path="imports"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <Imports />
              </Suspense>
            }
          />
          <Route
            path="playlists"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <Playlists />
              </Suspense>
            }
          />
          <Route
            path="playlists/:id"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <PlaylistDetail />
              </Suspense>
            }
          />
          <Route
            path="workflows"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <Workflows />
              </Suspense>
            }
          />
          <Route
            path="workflows/new"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <WorkflowEditor />
              </Suspense>
            }
          />
          <Route
            path="workflows/:id/edit"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <WorkflowEditor />
              </Suspense>
            }
          />
          <Route
            path="workflows/:id"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <WorkflowDetail />
              </Suspense>
            }
          />
          <Route
            path="workflows/:id/runs/:runId"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <WorkflowRunDetail />
              </Suspense>
            }
          />
          <Route
            path="library"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <Library />
              </Suspense>
            }
          />
          <Route
            path="library/:id"
            element={
              <Suspense fallback={<PageSkeleton />}>
                <TrackDetail />
              </Suspense>
            }
          />
          <Route
            path="settings"
            element={
              <Suspense fallback={<PageSkeleton />}>
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
