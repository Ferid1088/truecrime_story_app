"use client";

import Link from "next/link";
import { Download } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, formatTimecode, humanize, langLabel } from "@/lib/format";
import type { DocumentaryJob, Film, ProductionSummary } from "@/lib/types";
import { ResolutionBadge } from "@/components/status-badge";
import { FilmStateBadge, ProductionTypeBadge } from "@/components/lifecycle/shared";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useSubtitleTrack } from "../hooks";
import { type DocumentaryTabProps, Metric, StageIcon, stageLabel, summarizeDetail } from "../shared";

interface RenderTabProps extends DocumentaryTabProps {
  job: DocumentaryJob | null;
}

export function RenderTab({ caseId, settings, overview, job, refreshKey }: RenderTabProps) {
  // The film (Video record) of each render: YouTube title, status, publication.
  const films = useApi(() => api.listFilms({ case_id: caseId }), [caseId, refreshKey], { keepPrevious: true });
  const filmOf = (ps: ProductionSummary | null) =>
    ps ? (films.data ?? []).find((f) => f.production_script_id === ps.id) ?? null : null;
  return (
    <div className="space-y-4">
      <p className="max-w-3xl text-xs leading-5 text-muted-foreground">
        One MP4 per language with soft subtitles. A pilot render holds only the opening of the film; a
        full render the whole {settings.film_minutes.min}–{settings.film_minutes.max} minutes.
      </p>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {settings.languages.map((lang) => (
          <RenderCard
            key={lang}
            caseId={caseId}
            lang={lang}
            production={overview.languages[lang]?.production ?? null}
            film={filmOf(overview.languages[lang]?.production ?? null)}
            job={job}
          />
        ))}
      </div>
    </div>
  );
}

function RenderCard({
  caseId,
  lang,
  production,
  film,
  job,
}: {
  caseId: number;
  lang: string;
  production: ProductionSummary | null;
  film: Film | null;
  job: DocumentaryJob | null;
}) {
  const jobRender = job?.result.renders?.[lang];
  const youtubeTitle =
    film?.youtube_title ??
    (jobRender && production && jobRender.production_script_id === production.id ? jobRender.youtube_title : null);
  const stage = job?.languages.includes(lang) ? job.stages.find((s) => s.name === `render:${lang}`) : undefined;
  const render = production?.render ?? null;
  const videoUrl = render && production?.video_url ? apiFileUrl(production.video_url) : null;
  const srtUrl = render && production?.subtitles_url ? apiFileUrl(production.subtitles_url) : null;
  const vtt = useSubtitleTrack(srtUrl);
  const stageDetail = stage ? summarizeDetail(stage.detail) : null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {langLabel(lang)}
          {production && <Badge variant="outline">{production.mode}</Badge>}
        </CardTitle>
        {production ? (
          <Badge variant={render ? "success" : "info"}>{render ? "rendered" : humanize(production.status)}</Badge>
        ) : (
          <span className="text-[11px] text-muted-foreground">no production yet</span>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {stage && job && (
          <div className="flex items-start gap-2 rounded-md border border-border px-3 py-2 text-xs">
            <span className="mt-px">
              <StageIcon status={stage.status} />
            </span>
            <div className="min-w-0">
              <p>
                {stageLabel(stage.name)} · job #{job.id}
              </p>
              {stageDetail && (
                <p
                  className={cn(
                    "mt-0.5 break-words leading-4",
                    stage.status === "failed" ? "text-rose-600 dark:text-rose-400" : "text-muted-foreground",
                  )}
                >
                  {stageDetail}
                </p>
              )}
            </div>
          </div>
        )}

        {render && youtubeTitle && (
          <div className="space-y-1 rounded-md border border-border px-3 py-2 text-xs">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">YouTube title</p>
            <p className="text-sm font-medium">{youtubeTitle}</p>
            {film && (
              <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
                <FilmStateBadge state={film.state} />
                <ProductionTypeBadge type={film.production_type} />
                <span className="inline-flex items-center gap-1">
                  made while <ResolutionBadge status={film.status_at_production} />
                </span>
                {film.episode_number != null && <span>Episode {film.episode_number}</span>}
                <Link href={`/cases/${caseId}`} className="text-primary hover:underline">
                  Publish or archive in the case&apos;s Films tab
                </Link>
              </div>
            )}
          </div>
        )}

        {render && videoUrl ? (
          <>
            <video
              key={videoUrl}
              controls
              preload="metadata"
              className="aspect-video w-full rounded-md bg-black"
              aria-label={`${langLabel(lang)} ${production?.mode === "pilot" ? "pilot" : "film"} render`}
            >
              <source src={videoUrl} type="video/mp4" />
              {vtt && <track kind="subtitles" src={vtt} srcLang={lang} label={langLabel(lang)} default />}
            </video>
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
              <Metric label="Duration" value={formatTimecode(render.duration)} />
              <Metric label="Resolution" value={`${render.width}×${render.height}`} />
              <Metric label="Frame rate" value={`${render.fps} fps`} />
              <Metric label="Shots" value={render.shots} />
              <Metric label="Frames" value={render.frames.toLocaleString()} />
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs">
              <a href={videoUrl} download className="inline-flex items-center gap-1 text-primary hover:underline">
                <Download className="size-3.5" aria-hidden /> Download MP4
              </a>
              {srtUrl && (
                <a href={srtUrl} download className="inline-flex items-center gap-1 text-primary hover:underline">
                  <Download className="size-3.5" aria-hidden /> Download subtitles (.srt)
                </a>
              )}
              {production && (
                <span className="text-muted-foreground">
                  production v{production.version} · {formatDateTime(production.created_at)}
                </span>
              )}
            </div>
          </>
        ) : (
          <p className="text-xs leading-5 text-muted-foreground">
            {production
              ? "Not rendered yet — the render stage runs after the critique."
              : "Nothing to render yet — run a pilot in the Run tab."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
