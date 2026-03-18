import { DatabaseZap } from "lucide-react";

import { API_ERROR_CODES, ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";

import { EmptyState } from "./EmptyState";

export function isDatabaseUnavailable(error: unknown): error is ApiError {
  return (
    error instanceof ApiError &&
    error.code === API_ERROR_CODES.DATABASE_UNAVAILABLE
  );
}

export function DatabaseUnavailable() {
  return (
    <EmptyState
      icon={<DatabaseZap className="size-10" />}
      heading="Database unavailable"
      description="Cannot connect to PostgreSQL. Make sure the database is running, then reload."
      action={
        <Button size="sm" onClick={() => window.location.reload()}>
          Reload
        </Button>
      }
      role="alert"
    />
  );
}
