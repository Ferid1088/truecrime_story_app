"use client";

import { useEffect, useState } from "react";
import { api, apiFileUrl } from "@/lib/api";
import type { FilmOpeningView, OpeningsOverview } from "@/lib/types";
import { formatDate, isRtl, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/state";
import { LanguagePicker, type LanguageTabProps } from "../shared";

const STATUS_VARIANT = { valid: "success", needs_review: "warning", draft: "default" } as const;
const seconds = (value: number | null | undefined) => (value == null ? "—" : `${value.toFixed(1)} s`);

/** One language: the first seconds of the film, before the host's first appearance. Only what was made is shown (no voice or picture that does not exist). Read only: the voice and the texts are reviewed in the Voice Review and Farsi review tabs. */
function LanguageOpening({ view }: { view: FilmOpeningView }) {
  const dir = isRtl(view.language) ? "rtl" : "ltr";
  const test = `opening-${view.language}`;
  const variant = STATUS_VARIANT[view.status as keyof typeof STATUS_VARIANT] ?? "default";
  const pictures = view.scenes.filter((scene) => scene.picture_url != null);
  return (
    <div className="space-y-4" data-testid={test}>
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
            Opening — {langLabel(view.language)}
            <Badge variant={variant} data-testid={`${test}-status`}>{view.status.replace("_", " ")}</Badge>
            <Badge variant="outline">{view.kind === "master" ? "written once, from the English story" : "written again in this language"}</Badge>
            <Badge variant="outline">v{view.version}</Badge>
            {view.audio && <Badge variant="outline">{seconds(view.audio.seconds)}</Badge>}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {view.audio ? (
            <div className="space-y-1">
              <audio controls preload="none" src={apiFileUrl(view.audio.url)} className="w-full max-w-xl" data-testid={`${test}-audio`} />
              <p className="text-xs text-muted-foreground">Hook {seconds(view.audio.hook?.[1])} · context from {seconds(view.audio.context?.[0])} to {seconds(view.audio.context?.[1])}, with the music.</p>
            </div>
          ) : null}
          <div>
            <p className="text-xs font-medium">Hook (the first statement)</p>
            <p dir={dir} lang={view.language} className="text-base" data-testid={`${test}-hook`}>{view.hook}</p>
          </div>
          <div>
            <p className="text-xs font-medium">Context (what follows it)</p>
            <p dir={dir} lang={view.language} className="text-sm" data-testid={`${test}-context`}>{view.context}</p>
          </div>
          {view.issues.length > 0 && (
            <ul className="space-y-0.5 text-xs text-amber-700 dark:text-amber-400" data-testid={`${test}-issues`}>
              {view.issues.map((issue, i) => <li key={`${issue.type}-${i}`}>Check — {issue.type}{issue.fix ? `: ${issue.fix}` : ""}</li>)}
            </ul>
          )}
          <p className="text-xs text-muted-foreground">
            Changing the voice or the text: {view.language === "fa" ? "Farsi review → Opening (hook)" : "Voice Review"}.{view.created_at ? ` Written ${formatDate(view.created_at)}${view.model ? ` · ${view.model}` : ""}.` : ""}
          </p>
        </CardContent>
      </Card>

      {(view.music.mood || view.music.prompt) && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Music</CardTitle></CardHeader>
          <CardContent className="space-y-1 text-sm">
            {view.music.mood && <p>Mood: {view.music.mood}</p>}
            {view.music.prompt && <p className="text-xs text-muted-foreground">{view.music.prompt}</p>}
          </CardContent>
        </Card>
      )}

      {pictures.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Pictures of the opening</CardTitle></CardHeader>
          <CardContent className="grid gap-3 sm:grid-cols-2">
            {pictures.map((scene, i) => (
              <div key={i} className="space-y-1">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={apiFileUrl(scene.picture_url as string)} alt={`Picture ${i + 1} of the opening in ${langLabel(view.language)}`} className="w-full rounded border" />
                <p className="text-xs text-muted-foreground">{scene.text}</p>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      {view.candidates.length > 0 && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Hooks the director weighed</CardTitle></CardHeader>
          <CardContent className="space-y-1">
            {view.candidates.map((c, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
                <Badge variant={c.chosen ? "success" : "outline"}>{c.chosen ? "chosen" : c.kind}</Badge>
                <span>{c.text}</span>
                {c.why && <span className="text-xs text-muted-foreground">— {c.why}</span>}
              </div>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

/** The opening of each film, one tab per language whose opening has been made (its voice exists). Any other language is not shown. */
export function OpeningTab({ caseId, language, onLanguageChange, refreshKey }: LanguageTabProps) {
  const [data, setData] = useState<OpeningsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.openings(caseId).then((overview) => {
      if (live) {
        setData(overview);
        setError(null);
      }
    }).catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load the openings"));
    return () => { live = false; };
  }, [caseId, refreshKey]);

  const languages = data?.languages.map((l) => l.language) ?? [];
  const current = data?.languages.find((l) => l.language === language) ?? data?.languages[0];
  return (
    <div className="space-y-4">
      {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
      {data && languages.length === 0 && <EmptyState title="No opening has been created yet" description="The opening (the hook and the context before the host appears) is shown here once its voice has been made; a written text without a voice is not shown yet." />}
      {languages.length > 0 && <LanguagePicker languages={languages} value={current?.language ?? language} onChange={onLanguageChange} />}
      {current && <LanguageOpening key={`${current.language}-${current.version}-${current.status}`} view={current} />}
    </div>
  );
}
