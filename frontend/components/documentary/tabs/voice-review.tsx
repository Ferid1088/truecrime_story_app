"use client";

import { useEffect, useState } from "react";
import { langLabel } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/state";
import { BlockReview } from "./block-review";
import { LanguagePicker, type LanguageTabProps } from "../shared";

/** The owner's voice review: listen to the narration (and the host's voice and the film's opening) block by block, correct a block and rebuild it. */
export function VoiceReviewTab({ settings, overview, language, onLanguageChange }: LanguageTabProps) {
  const versionId = overview.languages[language]?.version_id ?? null;
  const [view, setView] = useState<"voice" | "host" | "opening">("voice");

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
          <div className="flex gap-2" role="tablist" aria-label="View">
            <Button size="sm" variant={view === "voice" ? "default" : "secondary"} onClick={() => setView("voice")}>Voice review</Button>
            {language === "fa" && <Button size="sm" variant={view === "host" ? "default" : "secondary"} onClick={() => setView("host")}>Host voice review</Button>}
            {language === "fa" && <Button size="sm" variant={view === "opening" ? "default" : "secondary"} onClick={() => setView("opening")}>Opening review</Button>}
          </div>
          {view === "voice" && (versionId != null ? <BlockReview key={versionId} versionId={versionId} /> : <EmptyState title="No narration yet" description="The voice review opens when this language has a narration." />)}
          {view === "host" && language === "fa" && (versionId != null ? <BlockReview key={`host-${versionId}`} versionId={versionId} kind="host" /> : <EmptyState title="No narration yet" description="The host voice review opens when this language has a narration." />)}
          {view === "opening" && language === "fa" && (versionId != null ? <BlockReview key={`opening-${versionId}`} versionId={versionId} kind="opening" /> : <EmptyState title="No narration yet" description="The opening review opens when this language has a narration." />)}
        </CardContent>
      </Card>
    </div>
  );
}
