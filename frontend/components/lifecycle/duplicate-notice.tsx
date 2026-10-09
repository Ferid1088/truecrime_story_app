"use client";

import Link from "next/link";
import { Copy } from "lucide-react";
import type { DuplicateConflict } from "@/lib/types";
import { Button } from "@/components/ui/button";

/**
 * The duplicate checker refused a new case (409): which record matched
 * and why, a link to it, and "Create anyway" (force).
 */
export function DuplicateNotice({
  conflict,
  onForce,
  forcing,
  forceLabel = "Create anyway",
}: {
  conflict: DuplicateConflict;
  onForce: () => void;
  forcing?: boolean;
  forceLabel?: string;
}) {
  const caseLink = conflict.matched_kind === "case" && conflict.matched_id != null;
  return (
    <div
      role="alert"
      className="space-y-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs leading-5"
    >
      <p className="flex items-start gap-1.5 font-medium text-amber-700 dark:text-amber-400">
        <Copy className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        {conflict.message || "This case already exists in the system."}
      </p>
      {conflict.matched_title && (
        <p>
          Matches{" "}
          {caseLink ? (
            <Link href={`/cases/${conflict.matched_id}`} className="font-medium text-primary hover:underline">
              {conflict.matched_title}
            </Link>
          ) : (
            <span className="font-medium">{conflict.matched_title}</span>
          )}
          {conflict.matched_state && <span className="text-muted-foreground"> ({conflict.matched_state})</span>}
        </p>
      )}
      <p className="text-muted-foreground">
        <span className="font-medium text-foreground">Why: </span>
        {conflict.reason}
      </p>
      <div className="flex flex-wrap gap-2">
        {caseLink && (
          <Link
            href={`/cases/${conflict.matched_id}`}
            className="inline-flex h-7 items-center rounded-md border border-border px-2.5 text-xs font-medium hover:bg-muted"
          >
            Open existing case
          </Link>
        )}
        <Button size="sm" variant="secondary" onClick={onForce} loading={forcing}>
          {forceLabel}
        </Button>
      </div>
    </div>
  );
}
