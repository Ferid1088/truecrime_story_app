"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { CaseOverview } from "@/components/case/overview";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { StoryPlanPanel } from "./story-plan-panel";
import { LanguagePipeline } from "./language-pipeline";

// The plan belongs to the shared draft: English, 90 minutes (config language_pipeline).
const DRAFT_LANGUAGE = "en";
const DRAFT_MINUTES = 90;

export function Studio() {
  const params = useSearchParams();
  const initialCase = params.get("case");
  const { data: cases, error, loading, refetch } = useApi(() => api.listCases());
  const [caseId, setCaseId] = useState<number | null>(initialCase ? Number(initialCase) : null);

  if (loading) return <Skeleton className="h-[70vh]" />;
  if (error) return <ErrorState message={error} />;
  if (!cases || cases.length === 0)
    return (
      <EmptyState
        title="No cases to write about"
        description="Create or discover a case first, add sources, and run research."
      />
    );

  const effectiveId = caseId ?? cases[0].id;
  return (
    <div>
      <PageHeader
        title="Story Studio"
        description="One shared English draft, then a native, independently inspected final text per language."
      />
      <div className="mb-6 max-h-80 overflow-y-auto">
        <CaseOverview
          idPrefix="studio"
          dateLabel="Created"
          selectedId={effectiveId}
          onOpen={setCaseId}
          items={cases.map((c) => ({
            id: c.id,
            title: c.title,
            date: c.created_at,
            archived: c.archived,
            archiveReason: c.archive_reason,
          }))}
          onArchive={async (id) => {
            await api.archiveCase(id);
            refetch();
          }}
          onRestore={async (id) => {
            await api.unarchiveCase(id);
            refetch();
          }}
        />
      </div>
      <div className="space-y-4">
        <StoryPlanPanel caseId={effectiveId} language={DRAFT_LANGUAGE} minutes={DRAFT_MINUTES} />
        <LanguagePipeline caseId={effectiveId} />
      </div>
    </div>
  );
}
