"use client";

import { useEffect } from "react";
import { ErrorState } from "@/components/ui";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Application route error", error);
  }, [error]);

  return (
    <ErrorState
      title="This view could not be loaded"
      headingLevel="h1"
      description="An unexpected application error occurred. No operation was retried automatically."
      action={
        <button type="button" className="btn btn-primary" onClick={reset}>
          Try again
        </button>
      }
    />
  );
}
