"use client";

import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { confidenceLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { EmptyState, ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export function TimelineTab({ caseId, refreshKey }: { caseId: number; refreshKey: number }) {
  const { data: items, error, loading, refetch } = useApi(
    () => api.listTimeline(caseId),
    [caseId, refreshKey],
  );

  if (loading)
    return (
      <div className="space-y-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
    );
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!items || items.length === 0)
    return (
      <EmptyState
        title="No dated events"
        description="The timeline is built from facts whose sources mention a specific date. Run research after adding sources."
      />
    );

  return (
    <div className="max-w-2xl">
      <ol className="relative border-l border-border pl-6">
        {items.map((f, i) => (
          <li key={f.id} className={cn("relative pb-6", i === items.length - 1 && "pb-0")}>
            <span className="absolute -left-[31px] top-0.5 flex size-4 items-center justify-center rounded-full border border-border bg-card">
              <span className="size-1.5 rounded-full bg-primary" />
            </span>
            <p className="font-mono text-xs font-medium text-primary">{f.event_date}</p>
            <p className="mt-1 text-sm leading-6" dir="auto">
              {f.claim}
            </p>
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <Badge variant="outline">{f.category}</Badge>
              <Badge
                variant={
                  confidenceLabel(f.confidence) === "high"
                    ? "success"
                    : confidenceLabel(f.confidence) === "medium"
                      ? "warning"
                      : "danger"
                }
              >
                {confidenceLabel(f.confidence)}
              </Badge>
              {f.disputed && <Badge variant="danger">disputed</Badge>}
              <span className="text-xs text-muted-foreground">
                {f.source_ids.length} source{f.source_ids.length === 1 ? "" : "s"}:{" "}
                {f.source_ids.map((id) => `#${id}`).join(", ")}
              </span>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
