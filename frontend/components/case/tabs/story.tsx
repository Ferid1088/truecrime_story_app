"use client";

import { LanguagePipeline } from "@/components/studio/language-pipeline";

export function StoryTab({ caseId }: { caseId: number }) {
  return <LanguagePipeline caseId={caseId} />;
}
