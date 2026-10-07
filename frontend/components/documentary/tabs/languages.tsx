"use client";

import { useState } from "react";
import { BookOpen } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatTimecode, humanize, isRtl, langLabel } from "@/lib/format";
import type { DocumentaryLanguage, FilmMinutesRange } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/state";
import { type DocumentaryTabProps, Metric, filmLengthIssue } from "../shared";

const GATE_LABELS: Record<string, string> = {
  meaning_changed: "Meaning changed",
  newsreader_tone: "Newsreader tone",
  not_enough_storytelling: "Not enough storytelling",
  sentences_too_long: "Sentences too long to speak",
  language_quality: "Language quality",
  output_purity: "Meta text or links in output",
  duration_out_of_range: "Length differs too much from the master",
};

const STATUS_VARIANT: Record<string, "success" | "warning" | "info" | "danger" | "outline"> = {
  ready: "success",
  needs_revision: "warning",
  draft: "info",
  failed: "danger",
  outdated: "outline",
};

export function LanguagesTab({ caseId, settings, overview }: DocumentaryTabProps) {
  const [reading, setReading] = useState<{ lang: string; versionId: number } | null>(null);

  return (
    <div>
      <p className="mb-4 max-w-3xl text-xs leading-5 text-muted-foreground">
        Each language gets its own spoken version — the story told the way a person tells it,
        natively, beat by beat, with the facts unchanged. Persian and Arabic read right to left.
      </p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {settings.languages.map((lang) => (
          <LanguageCard
            key={lang}
            lang={lang}
            data={overview.languages[lang] ?? null}
            range={settings.film_minutes}
            onRead={(versionId) => setReading({ lang, versionId })}
          />
        ))}
      </div>

      <Sheet
        open={reading != null}
        onClose={() => setReading(null)}
        title={reading ? `Spoken version · ${langLabel(reading.lang)}` : "Spoken version"}
        className="max-w-2xl"
      >
        {reading && <SpokenReader caseId={caseId} versionId={reading.versionId} />}
      </Sheet>
    </div>
  );
}

function LanguageCard({
  lang,
  data,
  range,
  onRead,
}: {
  lang: string;
  data: DocumentaryLanguage | null;
  range: FilmMinutesRange;
  onRead: (versionId: number) => void;
}) {
  if (!data) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>{langLabel(lang)}</CardTitle>
          <span className="text-[11px] text-muted-foreground">not generated</span>
        </CardHeader>
        <CardContent>
          <p className="text-xs leading-5 text-muted-foreground">
            No spoken version yet — every run writes one per selected language right after the
            blueprint and audio plan.
          </p>
        </CardContent>
      </Card>
    );
  }

  const gates = data.quality_gates;
  const lengthIssue = filmLengthIssue(data.estimated_film_minutes, range);
  const storyteller = data.storyteller_beats ?? 0;
  const beats = data.beats ?? 0;
  const prod = data.production;
  const perf = data.voice_performance;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {langLabel(lang)}
          <span className="text-xs font-normal text-muted-foreground">#{data.version_id}</span>
        </CardTitle>
        <Badge variant={STATUS_VARIANT[data.status] ?? "outline"}>{humanize(data.status)}</Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-3 gap-2">
          <Metric
            label="Est. film length"
            value={`${data.estimated_film_minutes.toFixed(1)} min`}
            tone={lengthIssue ? "danger" : undefined}
          />
          <Metric label="Storyteller beats" value={beats ? `${storyteller}/${beats}` : "—"} />
          <Metric
            label="Quality gates"
            value={gates ? (gates.pass ? "pass" : `${gates.failures.length} failed`) : "—"}
            tone={gates ? (gates.pass ? "success" : "danger") : undefined}
          />
        </div>
        {beats > 0 && (
          <div>
            <p className="mb-1 text-[11px] text-muted-foreground">
              Beats told like a storyteller (not a newsreader)
            </p>
            <Progress value={(storyteller / beats) * 100} className="h-1" />
          </div>
        )}
        {lengthIssue && (
          <p className="text-[11px] leading-4 text-rose-600 dark:text-rose-400">
            {lengthIssue === "short"
              ? `Too short for a full film (needs at least ${range.min} min). Pilots are still allowed.`
              : `Longer than ${range.max} min — too long for one film.`}
          </p>
        )}
        {gates && !gates.pass && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Failed quality gates">
            {gates.failures.map((f) => (
              <li key={f}>
                <Badge variant="danger">{GATE_LABELS[f] ?? humanize(f)}</Badge>
              </li>
            ))}
          </ul>
        )}
        <p className="text-xs text-muted-foreground">
          {perf
            ? `Narrator performance: ${humanize(perf.status)}${perf.stats ? ` · ${perf.stats.directed}/${perf.stats.sentences} sentences directed` : ""}`
            : "Narrator performance not directed yet."}
        </p>
        <p className="text-xs text-muted-foreground">
          {prod
            ? `Production v${prod.version} · ${prod.mode} · ${humanize(prod.status)} · ${formatTimecode(prod.duration_seconds)}`
            : "No production script yet."}
        </p>
        <Button size="sm" variant="secondary" onClick={() => onRead(data.version_id)}>
          <BookOpen className="size-3.5" /> Read spoken text
        </Button>
      </CardContent>
    </Card>
  );
}

function SpokenReader({ caseId, versionId }: { caseId: number; versionId: number }) {
  const { data, error, loading, refetch } = useApi(
    () => api.getStoryVersion(caseId, versionId),
    [caseId, versionId],
  );
  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="h-40" />
      </div>
    );
  }
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!data) return null;

  const paragraphs = data.story_text
    .split(/\n{2,}|\n/)
    .map((p) => p.trim())
    .filter(Boolean);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border pb-3 text-xs text-muted-foreground">
        <Badge variant="accent">v{data.version}</Badge>
        <span>{langLabel(data.language)}</span>
        <span className="tabular-nums">{data.word_count.toLocaleString()} words</span>
        {data.native_quality_score != null && (
          <span>
            Storyteller <span className="font-medium text-foreground">{Math.round(data.native_quality_score)}%</span>
          </span>
        )}
        {data.semantic_consistency_score != null && (
          <span>
            Meaning kept{" "}
            <span className="font-medium text-foreground">{Math.round(data.semantic_consistency_score)}%</span>
          </span>
        )}
      </div>
      <article
        lang={data.language}
        dir={isRtl(data.language) ? "rtl" : "ltr"}
        className="max-w-prose text-[15px] leading-8 text-foreground/95"
      >
        {paragraphs.map((p, i) => (
          <p key={i} className="mb-5">
            {p}
          </p>
        ))}
      </article>
    </div>
  );
}
