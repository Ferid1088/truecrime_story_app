"use client";

import type { PolicyWarning } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function PolicyWarningNotice({
  warning,
  className,
}: {
  warning?: PolicyWarning | null;
  className?: string;
}) {
  if (!warning) return null;
  const danger = warning.severity === "error";
  return (
    <div
      role="alert"
      className={cn(
        "rounded-md border px-3 py-2 text-xs leading-5",
        danger
          ? "border-rose-500/30 bg-rose-500/5 text-rose-600 dark:text-rose-400"
          : "border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-400",
        className,
      )}
    >
      <p className="font-medium">{warning.code}</p>
      <p>{warning.message}</p>
      {warning.actions.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1.5">
          {warning.actions.map((action) => (
            <Button key={action} type="button" size="sm" variant="outline" disabled>
              {action.replaceAll("_", " ")}
            </Button>
          ))}
        </div>
      )}
    </div>
  );
}
