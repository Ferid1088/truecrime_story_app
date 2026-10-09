"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Clapperboard, CornerDownRight, ExternalLink, MapPin, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDate, langLabel } from "@/lib/format";
import type { ArchiveCase, ArchiveFilter, Film } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/state";
import { CaseStatusBadge, ResolutionBadge, resolutionLabel } from "@/components/status-badge";
import { FilmStateBadge, ProductionTypeBadge, filmTitle, openingLabel } from "@/components/lifecycle/shared";
import { cn } from "@/lib/utils";

const FILTERS: { value: ArchiveFilter; label: string }[] = [
  { value: "ALL", label: "All" },
  { value: "SOLVED", label: "Solved" },
  { value: "UNSOLVED", label: "Unsolved" },
  { value: "STATUS_UNDER_REVIEW", label: "Under review" },
];

function parseFilter(value: string | null): ArchiveFilter {
  const v = (value ?? "").toUpperCase();
  return FILTERS.some((f) => f.value === v) ? (v as ArchiveFilter) : "ALL";
}

export default function ArchivePage() {
  return (
    <Suspense fallback={<Skeleton className="h-96" />}>
      <ArchiveView />
    </Suspense>
  );
}

function ArchiveView() {
  const searchParams = useSearchParams();
  const filter = parseFilter(searchParams.get("status"));
  const [tick, setTick] = useState(0);

  // Counts come from the unfiltered archive (the API counts only what it returns).
  const all = useApi(() => api.archive("ALL"), [tick], { keepPrevious: true });
  const filtered = useApi(
    () => (filter === "ALL" ? Promise.resolve(null) : api.archive(filter)),
    [filter, tick],
  );
  const state = filter === "ALL" ? all : filtered;
  const cases = filter === "ALL" ? all.data?.cases : filtered.data?.cases;
  const counts = all.data?.counts;
  const total = all.data?.cases.length;

  function setFilter(value: ArchiveFilter) {
    const params = new URLSearchParams(searchParams.toString());
    if (value === "ALL") params.delete("status");
    else params.set("status", value);
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }

  return (
    <div>
      <PageHeader
        title="Archive"
        description="Every case the channel has covered or archived — solved and unsolved stay clearly apart, with their films."
        actions={
          <Button variant="ghost" size="sm" onClick={() => setTick((t) => t + 1)} loading={state.loading && !!cases}>
            {!(state.loading && cases) && <RefreshCw className="size-3.5" />} Refresh
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap gap-1" role="group" aria-label="Filter by case status">
        {FILTERS.map((f) => {
          const n = f.value === "ALL" ? total : counts?.[f.value];
          return (
            <button
              key={f.value}
              type="button"
              aria-pressed={filter === f.value}
              onClick={() => setFilter(f.value)}
              className={cn(
                "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
                filter === f.value
                  ? "border-primary bg-accent text-accent-foreground"
                  : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {f.label}
              {n != null && <span className="tabular-nums opacity-70">{n}</span>}
            </button>
          );
        })}
      </div>

      {!cases && state.error ? (
        <ErrorState message={state.error} onRetry={state.refetch} />
      ) : !cases ? (
        <div className="space-y-3">
          <Skeleton className="h-36" />
          <Skeleton className="h-36" />
        </div>
      ) : cases.length === 0 ? (
        <EmptyState
          title={filter === "ALL" ? "The archive is empty" : `No ${resolutionLabel(filter).toLowerCase()} cases in the archive`}
          description="A case enters the archive when one of its films is published or archived, or when the case itself is archived."
        />
      ) : (
        <ul className="space-y-3">
          {cases.map((c) => (
            <li key={c.id}>
              <ArchiveCaseCard c={c} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ArchiveCaseCard({ c }: { c: ArchiveCase }) {
  const byId = new Map(c.films.map((f) => [f.id, f]));
  return (
    <Card>
      <CardHeader className="flex-wrap items-start gap-3">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2 text-base leading-snug">
            <Link href={`/cases/${c.id}`} className="hover:text-primary">
              {c.title}
            </Link>
            <ResolutionBadge status={c.resolution_status} confidence={c.resolution_confidence} size="lg" />
          </CardTitle>
          <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <CaseStatusBadge status={c.status} />
            <span>Case #{c.id}</span>
            {c.location && (
              <span className="inline-flex items-center gap-1">
                <MapPin className="size-3" aria-hidden /> {c.location}
              </span>
            )}
            {c.resolution_checked_at && <span>status checked {formatDate(c.resolution_checked_at)}</span>}
          </div>
        </div>
        <Link
          href={`/documentary/${c.id}`}
          className="inline-flex shrink-0 items-center gap-1 text-xs text-primary hover:underline"
        >
          <Clapperboard className="size-3.5" aria-hidden /> Documentary
        </Link>
      </CardHeader>
      <CardContent className="space-y-3">
        {c.resolution_summary && <p className="max-w-3xl text-sm leading-6 text-foreground/90">{c.resolution_summary}</p>}
        {c.films.length === 0 ? (
          <p className="text-xs text-muted-foreground">No film recorded — the case itself was archived.</p>
        ) : (
          <ul className="divide-y divide-border rounded-md border border-border">
            {c.films.map((f) => (
              <ArchiveFilm
                key={f.id}
                film={f}
                original={f.original_video_id != null ? byId.get(f.original_video_id) ?? null : null}
                followUps={c.films.filter((x) => x.original_video_id === f.id)}
              />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function YouTubeLink({ film }: { film: Film }) {
  if (!film.youtube_url) return null;
  return (
    <a
      href={film.youtube_url}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 text-primary hover:underline"
    >
      <ExternalLink className="size-3" aria-hidden /> YouTube
    </a>
  );
}

function ArchiveFilm({ film: f, original, followUps }: { film: Film; original: Film | null; followUps: Film[] }) {
  return (
    <li className={cn("space-y-1 px-3 py-2 text-xs", f.production_type === "follow_up" && "bg-indigo-500/[0.03]")}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{filmTitle(f)}</span>
        <FilmStateBadge state={f.state} />
        <ProductionTypeBadge type={f.production_type} />
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
        <span>{langLabel(f.language)}</span>
        {f.episode_number != null && <span>Episode {f.episode_number}</span>}
        <span>{f.published_at ? `published ${formatDate(f.published_at)}` : "not published"}</span>
        <span className="inline-flex items-center gap-1">
          made while <ResolutionBadge status={f.status_at_production} />
        </span>
        {f.status_at_publication && (
          <span className="inline-flex items-center gap-1">
            published while <ResolutionBadge status={f.status_at_publication} />
          </span>
        )}
        {f.opening_strategy && <span>opening: {openingLabel(f.opening_strategy)}</span>}
        <YouTubeLink film={f} />
      </div>
      {f.production_type === "follow_up" && (
        <p className="flex flex-wrap items-center gap-1 text-muted-foreground">
          <CornerDownRight className="size-3" aria-hidden />
          Update to{" "}
          <span className="text-foreground">
            {original ? filmTitle(original) : f.original_video_id != null ? `film #${f.original_video_id}` : "an earlier video"}
          </span>
          {original?.episode_number != null && <span>(Episode {original.episode_number})</span>}
          {original && <YouTubeLink film={original} />}
        </p>
      )}
      {followUps.map((x) => (
        <p key={x.id} className="flex flex-wrap items-center gap-1 text-muted-foreground">
          <CornerDownRight className="size-3" aria-hidden />
          <Badge variant="accent">Follow-up</Badge>
          <span className="text-foreground">{filmTitle(x)}</span>
          {x.published_at && <span>· published {formatDate(x.published_at)}</span>}
          <YouTubeLink film={x} />
        </p>
      ))}
    </li>
  );
}
