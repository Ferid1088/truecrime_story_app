"use client";

import { useEffect } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <AlertTriangle className="size-6 text-rose-500" />
      <p className="text-sm font-medium">Something went wrong</p>
      <p className="max-w-md text-xs text-muted-foreground">
        {process.env.NODE_ENV === "development"
          ? error.message
          : "An unexpected error occurred while rendering this page."}
      </p>
      <Button variant="outline" size="sm" onClick={reset}>
        Retry
      </Button>
    </div>
  );
}
