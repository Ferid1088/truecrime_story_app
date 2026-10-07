"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { FilmList } from "@/components/lifecycle/films";

/** The films made of this case (one per language), with publish/archive. */
export function FilmsTab({ caseId }: { caseId: number }) {
  const { data, error, loading, refetch } = useApi(() => api.listFilms({ case_id: caseId }), [caseId], {
    keepPrevious: true,
  });

  if (!data && loading) return <TableSkeleton rows={3} cols={4} />;
  if (!data && error) return <ErrorState message={error} onRetry={refetch} />;
  if (!data || data.length === 0)
    return (
      <EmptyState
        title="No films yet"
        description="A film is recorded when a documentary renders: its YouTube title, the case status at production and the opening it uses."
        action={
          <Link href={`/documentary/${caseId}`} className="text-sm text-primary hover:underline">
            Open the documentary workspace
          </Link>
        }
      />
    );

  const published = data.filter((f) => f.state === "published").length;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Films</CardTitle>
        <span className="text-xs text-muted-foreground">
          {data.length} film{data.length === 1 ? "" : "s"} · {published} published
        </span>
      </CardHeader>
      <CardContent className="p-0">
        {error && <p className="px-4 pt-3 text-xs text-rose-500">Could not refresh: {error}</p>}
        <FilmList films={data} onChanged={refetch} />
      </CardContent>
    </Card>
  );
}
