"use client";

import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Select } from "@/components/ui/select";
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
  const { data: cases, error, loading } = useApi(() => api.listCases());
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
      <div className="mb-4 max-w-sm">
        <label className="mb-1 block text-xs font-medium text-muted-foreground">Case</label>
        <Select value={effectiveId} onChange={(e) => setCaseId(Number(e.target.value))}>
          {cases.map((c) => (
            <option key={c.id} value={c.id}>
              {c.title}
            </option>
          ))}
        </Select>
      </div>
      <div className="space-y-4">
        <StoryPlanPanel caseId={effectiveId} language={DRAFT_LANGUAGE} minutes={DRAFT_MINUTES} />
        <LanguagePipeline caseId={effectiveId} />
      </div>
    </div>
  );
}
