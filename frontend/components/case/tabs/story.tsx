"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { PenLine } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { StoryFull } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { StoryReader } from "@/components/story/reader";
import { QualityPanel } from "@/components/story/quality-panel";
import { VersionList } from "@/components/story/version-list";

export function StoryTab({ caseId }: { caseId: number }) {
  const { data: versions, error, loading, refetch } = useApi(() => api.listStories(caseId), [caseId]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [story, setStory] = useState<StoryFull | null>(null);

  const activeId = selectedId ?? versions?.[0]?.id ?? null;

  useEffect(() => {
    if (activeId == null) return;
    let cancelled = false;
    api
      .getStoryVersion(caseId, activeId)
      .then((s) => {
        if (!cancelled) setStory(s);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [caseId, activeId]);

  if (loading) return <Skeleton className="h-72" />;
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!versions || versions.length === 0)
    return (
      <EmptyState
        title="No story yet"
        description="Once research is complete, generate a long-form story in the Story Studio."
        action={
          <Link href={`/studio?case=${caseId}`}>
            <Button size="sm">
              <PenLine className="size-3.5" /> Open Story Studio
            </Button>
          </Link>
        }
      />
    );

  const current = story && story.id === activeId ? story : null;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[220px_1fr_260px]">
      <div>
        <VersionList
          versions={versions}
          selectedId={activeId}
          onSelect={(v) => setSelectedId(v.id)}
        />
        <Link href={`/studio?case=${caseId}`} className="mt-3 block">
          <Button variant="secondary" size="sm" className="w-full">
            <PenLine className="size-3.5" /> Open in Studio
          </Button>
        </Link>
      </div>
      <Card>
        <CardContent className="p-5">
          {current ? <StoryReader story={current} /> : <Skeleton className="h-64" />}
        </CardContent>
      </Card>
      <Card className="h-fit">
        <CardHeader>
          <CardTitle>Story Quality</CardTitle>
        </CardHeader>
        <CardContent>{current && <QualityPanel story={current} />}</CardContent>
      </Card>
    </div>
  );
}
