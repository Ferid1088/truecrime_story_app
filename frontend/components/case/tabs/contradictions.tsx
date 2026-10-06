"use client";

import { AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";

const SEVERITY: Record<string, "danger" | "warning" | "default"> = {
  high: "danger",
  medium: "warning",
  low: "default",
};

export function ContradictionsTab({ caseId, refreshKey }: { caseId: number; refreshKey: number }) {
  const { data: items, error, loading, refetch } = useApi(
    () => api.listContradictions(caseId),
    [caseId, refreshKey],
  );
  const { data: sources } = useApi(() => api.listSources(caseId), [caseId]);

  const sourceTitle = (id: number) => sources?.find((s) => s.id === id)?.title ?? `#${id}`;

  if (loading)
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-28" />
        ))}
      </div>
    );
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!items || items.length === 0)
    return (
      <EmptyState
        title="No contradictions detected"
        description="Contradictions across sources appear here after research runs."
      />
    );

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {items.map((c) => (
        <Card key={c.id}>
          <CardContent className="p-4">
            <div className="mb-2 flex items-start justify-between gap-3">
              <p className="flex items-center gap-2 text-sm font-medium">
                <AlertTriangle className="size-4 shrink-0 text-amber-500" />
                {c.topic}
              </p>
              <Badge variant={SEVERITY[c.severity] ?? "default"}>{c.severity}</Badge>
            </div>
            <p className="text-sm leading-6 text-foreground/90" dir="auto">
              {c.description}
            </p>
            {c.source_ids.length > 0 && (
              <div className="mt-3 border-t border-border pt-2">
                <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Involved sources
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {c.source_ids.map((id) => (
                    <Badge key={id} variant="outline" className="max-w-48 truncate">
                      #{id} {sourceTitle(id)}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            <p className="mt-2 text-[11px] italic text-muted-foreground">
              Research data — not automatically treated as fact in the story.
            </p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
