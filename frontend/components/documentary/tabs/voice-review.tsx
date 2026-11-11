"use client";

import { useEffect } from "react";
import { langLabel } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/state";
import { BlockReview } from "./block-review";
import { LanguagePicker, type LanguageTabProps } from "../shared";

/** The owner's voice review of the languages other than Persian: listen to the narration block by block, correct a block and rebuild it. Every Persian review is in the Farsi review tab. */
export function VoiceReviewTab({ settings, overview, language, onLanguageChange }: LanguageTabProps) {
  const versionId = overview.languages[language]?.version_id ?? null;

  // The Monitoring shortcut names the language whose narration waits for the voice review (asked for once).
  useEffect(() => {
    try {
      const wanted = window.sessionStorage.getItem("voice-review-language");
      if (wanted) {
        window.sessionStorage.removeItem("voice-review-language");
        onLanguageChange(wanted);
      }
    } catch { /* the tab keeps its language */ }
  }, [onLanguageChange]);

  // The tab opens on the language that has a narration to listen to.
  const fallback = versionId == null ? settings.languages.find((l) => overview.languages[l] != null) : undefined;
  useEffect(() => {
    if (fallback) onLanguageChange(fallback);
  }, [fallback, onLanguageChange]);

  return (
    <div className="space-y-4">
      <LanguagePicker languages={settings.languages} value={language} onChange={onLanguageChange} isAvailable={(l) => overview.languages[l] != null} />
      <Card>
        <CardHeader>
          <CardTitle>Voice review — {langLabel(language)}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {language === "fa" ? (
            <EmptyState title="The Persian reviews are in the Farsi review tab" description="The long film's voice, the host's voice, the opening and the short clips are all reviewed there." />
          ) : versionId != null ? (
            <BlockReview key={versionId} versionId={versionId} />
          ) : (
            <EmptyState title="No narration yet" description="The voice review opens when this language has a narration." />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
