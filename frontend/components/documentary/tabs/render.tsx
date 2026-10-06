"use client";

import { Download } from "lucide-react";
import { apiFileUrl } from "@/lib/api";
import { formatDateTime, formatTimecode, humanize, langLabel } from "@/lib/format";
import type { DocumentaryJob, ProductionSummary } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { useSubtitleTrack } from "../hooks";
import { type DocumentaryTabProps, Metric, StageIcon, stageLabel, summarizeDetail } from "../shared";

interface RenderTabProps extends DocumentaryTabProps {
  job: DocumentaryJob | null;
}

export function RenderTab({ settings, overview, job }: RenderTabProps) {
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
            lang={lang}
            production={overview.languages[lang]?.production ?? null}
            job={job}
          />
        ))}
      </div>
    </div>
  );
}

function RenderCard({
  lang,
  production,
  job,
}: {
  lang: string;
  production: ProductionSummary | null;
  job: DocumentaryJob | null;
}) {
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
