"use client";

import { useEffect, useState } from "react";
import { api, nullIfNotFound } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { DocumentaryJob } from "@/lib/types";
import { finishedStages, isJobActive } from "./shared";

const POLL_INTERVAL_MS = 3000;

function timestamp(job: DocumentaryJob): number {
  const t = Date.parse(job.updated_at ?? "");
  return Number.isNaN(t) ? 0 : t;
}

/**
 * The job to show: a fresher polled copy of the same job wins; an active
 * polled job wins over an older-but-settled one; otherwise the newest id.
 */
function pickJob(polled: DocumentaryJob | null, latest: DocumentaryJob | null) {
  if (!polled) return latest;
  if (!latest) return polled;
  if (polled.id === latest.id) return timestamp(latest) > timestamp(polled) ? latest : polled;
  if (isJobActive(polled)) return polled;
  return polled.id > latest.id ? polled : latest;
}

/**
 * Follows the case's current documentary job: polls it every 3 s while it
 * is queued/running/cancelling and calls `onProgress` whenever a stage
 * finishes or the status changes, so the overview (and every tab fed by
 * it) refreshes as work lands. `track` replaces the followed job — after
 * starting, cancelling or resuming one.
 */
export function useJobPoller(latest: DocumentaryJob | null, onProgress: () => void) {
  const [polled, setPolled] = useState<DocumentaryJob | null>(null);
  const [polls, setPolls] = useState(0);
  const [pollError, setPollError] = useState<string | null>(null);

  const job = pickJob(polled, latest);
  const id = job?.id ?? null;
  const active = isJobActive(job);
  const status = job?.status;
  const finished = job ? finishedStages(job) : 0;

  useEffect(() => {
    if (id == null || !active) return;
    let cancelled = false;
    const timer = setTimeout(() => {
      api
        .documentaryJob(id)
        .then((next) => {
          if (cancelled) return;
          setPolled(next);
          setPollError(null);
          setPolls((n) => n + 1);
          if (next.status !== status || finishedStages(next) !== finished) onProgress();
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          setPollError(e instanceof Error ? e.message : String(e));
          setPolls((n) => n + 1);
        });
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id, active, status, finished, polls, onProgress]);

  return { job, track: setPolled, pollError: active ? pollError : null };
}

/**
 * Latest production script of one language for the chosen master. Skips the
 * request when the overview says none exists; keeps the previous script on
 * screen while a background refresh loads (callers check `language`).
 */
export function useProduction(
  caseId: number,
  language: string,
  versionId: number | null,
  exists: boolean,
  refreshKey: number,
) {
  return useApi(
    () =>
      exists
        ? nullIfNotFound(api.documentaryProduction(caseId, language, versionId))
        : Promise.resolve(null),
    [caseId, language, versionId, exists, refreshKey],
    { keepPrevious: true },
  );
}

/** SRT → WebVTT: browsers only play WebVTT in <track>. */
function srtToVtt(srt: string): string {
  const body = srt
    .replace(/\r\n?/g, "\n")
    .replace(/(\d{2}:\d{2}:\d{2}),(\d{3})/g, "$1.$2")
    .trim();
  return `WEBVTT\n\n${body}\n`;
}

/**
 * Fetches the render's .srt and exposes it as a same-origin WebVTT object
 * URL for a <track>. Returns null until loaded or when unavailable.
 */
export function useSubtitleTrack(srtUrl: string | null): string | null {
  const [track, setTrack] = useState<{ src: string; vtt: string | null } | null>(null);

  useEffect(() => {
    if (!srtUrl) return;
    let cancelled = false;
    let objectUrl: string | null = null;
    fetch(srtUrl)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(String(r.status)))))
      .then((srt) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(new Blob([srtToVtt(srt)], { type: "text/vtt" }));
        setTrack({ src: srtUrl, vtt: objectUrl });
      })
      .catch(() => {
        if (!cancelled) setTrack({ src: srtUrl, vtt: null });
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [srtUrl]);

  return track && track.src === srtUrl ? track.vtt : null;
}

/** Calls `refetch` every `intervalMs` while `active` (e.g. while jobs run). */
export function usePolling(refetch: () => void, active: boolean, intervalMs: number) {
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(refetch, intervalMs);
    return () => clearInterval(timer);
  }, [refetch, active, intervalMs]);
}
