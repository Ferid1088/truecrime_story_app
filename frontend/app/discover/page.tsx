"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CalendarDays, Compass, Globe, History, MapPin, Sparkles, Users } from "lucide-react";
import { api, duplicateConflict, pollResearchJob } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { DiscoveryCandidate, DuplicateConflict, SelectionStats } from "@/lib/types";
import { LANGUAGES, formatDateTime, humanize, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/state";
import { ResolutionBadge } from "@/components/status-badge";
import { Skeleton } from "@/components/ui/skeleton";
import { DuplicateNotice } from "@/components/lifecycle/duplicate-notice";
import { Collapsible, FieldLabel, SourceLinks } from "@/components/lifecycle/shared";
import { cn } from "@/lib/utils";

const LANG_OPTIONS = ["en", "de", "fa", "ar", "other"];
const COUNTS = [5, 10, 20];
/** Open suggestions of earlier runs shown at most. */
const HISTORY_LIMIT = 20;

export default function DiscoverPage() {
  const router = useRouter();
  const [count, setCount] = useState(5);
  const [langs, setLangs] = useState<string[]>(["en"]);
  const [theme, setTheme] = useState("true crime");
  const [preferUndercovered, setPreferUndercovered] = useState(true);
  const [requireMulti, setRequireMulti] = useState(false);
  const [searchYt, setSearchYt] = useState(true);
  const [searchWeb, setSearchWeb] = useState(true);
  const [avoidExisting, setAvoidExisting] = useState(true);
  const [includeUnsolved, setIncludeUnsolved] = useState(false);

  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [rejected, setRejected] = useState<DiscoveryCandidate[]>([]);
  const [stats, setStats] = useState<SelectionStats | null>(null);
  const [fromHistory, setFromHistory] = useState(false);
  const [ignored, setIgnored] = useState<Set<number>>(new Set());
  const [conflicts, setConflicts] = useState<Record<number, DuplicateConflict>>({});
  const [skippedDupes, setSkippedDupes] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [jobStage, setJobStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<{ id: number; force: boolean } | null>(null);
  const pollAbort = useRef<AbortController | null>(null);

  // Suggestions of earlier runs nobody acted on yet (offered, not shown by default).
  const history = useApi(() => api.discoveryHistory("suggested"), []);
  const openHistory = (history.data ?? []).slice(0, HISTORY_LIMIT);

  useEffect(() => () => pollAbort.current?.abort(), []);

  const toggleLang = (l: string) =>
    setLangs((prev) => (prev.includes(l) ? prev.filter((x) => x !== l) : [...prev, l]));

  async function discover() {
    setRunning(true);
    setError(null);
    setJobStage("Starting discovery job…");
    pollAbort.current?.abort();
    const ctl = new AbortController();
    pollAbort.current = ctl;
    try {
      const res = await api.discover({
        count,
        languages: langs.length ? langs : ["en"],
        theme,
        prefer_undercovered: preferUndercovered,
        require_multiple_sources: requireMulti,
        search_web: searchWeb,
        search_youtube: searchYt,
        avoid_existing: avoidExisting,
        include_unsolved: includeUnsolved,
      });
      if (res.job_id != null) {
        const job = await pollResearchJob(res.job_id, {
          signal: ctl.signal,
          onUpdate: (j) =>
            setJobStage(
              j.status === "queued"
                ? "Discovery job queued…"
                : j.current_stage === "verifying_status"
                  ? "Verifying whether the best candidates are solved…"
                  : "Research is searching the web and deduplicating against your archive…",
            ),
        });
        if (job.status === "failed") {
          throw new Error(job.error || "Research provider unavailable");
        }
        setCandidates(job.result?.candidates ?? []);
        setRejected(job.result?.rejected ?? []);
        setStats(job.result?.selection_stats ?? null);
        setSkippedDupes(job.result?.skipped_duplicates ?? null);
      } else {
        const result = res.result as { candidates?: DiscoveryCandidate[] } | undefined;
        setCandidates(result?.candidates ?? []);
        setRejected([]);
        setStats(null);
        setSkippedDupes(null);
      }
      setFromHistory(false);
      setIgnored(new Set());
      setConflicts({});
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setRunning(false);
      setJobStage(null);
    }
  }

  function showHistory() {
    setCandidates(openHistory);
    setRejected([]);
    setStats(null);
    setSkippedDupes(null);
    setFromHistory(true);
    setIgnored(new Set());
    setConflicts({});
  }

  async function investigate(c: DiscoveryCandidate, force = false) {
    setBusy({ id: c.candidate_id, force });
    setError(null);
    try {
      const res = await api.investigate(c.candidate_id, langs.includes("fa") ? "fa" : "en", force);
      router.push(`/cases/${res.id}`);
    } catch (e) {
      const dup = duplicateConflict(e);
      if (dup) setConflicts((prev) => ({ ...prev, [c.candidate_id]: dup }));
      else setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  async function ignore(c: DiscoveryCandidate) {
    setBusy({ id: c.candidate_id, force: false });
    try {
      await api.ignoreCandidate(c.candidate_id);
      setIgnored((prev) => new Set(prev).add(c.candidate_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const visible = candidates.filter((c) => !ignored.has(c.candidate_id));
  const checked = stats?.checked_against;

  return (
    <div>
      <PageHeader
        title="Find New Cases"
        description="Recent, solved cases nobody has covered yet — each suggestion says why, with its status evidence."
      />

      <Card className="mb-6">
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">Number of cases</p>
              <div className="flex rounded-md border border-border p-0.5">
                {COUNTS.map((n) => (
                  <button
                    key={n}
                    onClick={() => setCount(n)}
                    className={cn(
                      "flex-1 cursor-pointer rounded px-3 py-1 text-sm transition-colors",
                      count === n
                        ? "bg-primary text-primary-foreground"
                        : "text-muted-foreground hover:text-foreground",
                    )}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">Languages</p>
              <div className="flex flex-wrap gap-x-3 gap-y-1.5 pt-1">
                {LANG_OPTIONS.map((l) => (
                  <Checkbox
                    key={l}
                    checked={langs.includes(l)}
                    onChange={() => toggleLang(l)}
                    label={langLabel(l)}
                  />
                ))}
              </div>
            </div>
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">Theme</p>
              <Input value={theme} onChange={(e) => setTheme(e.target.value)} placeholder="true crime" />
            </div>
          </div>

          <div className="flex flex-wrap gap-x-5 gap-y-2 border-t border-border pt-4">
            <Checkbox
              checked={preferUndercovered}
              onChange={() => setPreferUndercovered((v) => !v)}
              label="Prefer under-covered cases"
            />
            <Checkbox
              checked={requireMulti}
              onChange={() => setRequireMulti((v) => !v)}
              label="Require multiple independent sources"
            />
            <Checkbox
              checked={searchYt}
              onChange={() => setSearchYt((v) => !v)}
              label="Search YouTube"
            />
            <Checkbox
              checked={searchWeb}
              onChange={() => setSearchWeb((v) => !v)}
              label="Search web"
            />
            <Checkbox
              checked={avoidExisting}
              onChange={() => setAvoidExisting((v) => !v)}
              label="Avoid cases already in database"
            />
            <Checkbox
              checked={includeUnsolved}
              onChange={() => setIncludeUnsolved((v) => !v)}
              label="Include unsolved cases"
              title="By default only solved (or not yet verified) cases are suggested; unsolved ones are listed under “Not suggested”."
            />
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={discover} loading={running} disabled={running}>
              <Sparkles className="size-4" />
              Discover New Cases
            </Button>
            {running && (
              <span className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="size-2 animate-pulse rounded-full bg-amber-500" />
                {jobStage ?? "Working…"}
              </span>
            )}
            {skippedDupes != null && skippedDupes > 0 && (
              <span className="text-xs text-muted-foreground">
                {skippedDupes} duplicate{skippedDupes === 1 ? "" : "s"} rejected
              </span>
            )}
            {!running && stats && (
              <span className="text-xs text-muted-foreground">
                {checked && (checked.cases != null || checked.suggestions != null) &&
                  `Checked against ${checked.cases ?? 0} case${checked.cases === 1 ? "" : "s"} and ${
                    checked.suggestions ?? 0
                  } earlier suggestion${checked.suggestions === 1 ? "" : "s"}`}
                {stats.verified ? ` · ${stats.verified} status${stats.verified === 1 ? "" : "es"} verified` : ""}
                {stats.filtered_unsolved ? ` · ${stats.filtered_unsolved} unsolved filtered` : ""}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="mb-4 rounded-md border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-600 dark:text-rose-400">
          {error}
        </div>
      )}

      {running && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-44" />
          ))}
        </div>
      )}

      {!running && visible.length === 0 && !error && candidates.length === 0 && (
        <EmptyState
          title="No candidates yet"
          description="Configure the discovery options above and run the Discovery Agent."
          action={
            openHistory.length > 0 ? (
              <Button variant="outline" size="sm" onClick={showHistory}>
                <History className="size-3.5" />
                Show {openHistory.length} open suggestion{openHistory.length === 1 ? "" : "s"} from earlier runs
              </Button>
            ) : undefined
          }
        />
      )}

      {!running && candidates.length > 0 && visible.length === 0 && (
        <EmptyState title="All candidates handled" description="Every suggested case was investigated or ignored." />
      )}

      {!running && fromHistory && visible.length > 0 && (
        <p className="mb-3 flex items-center gap-1.5 text-xs text-muted-foreground">
          <History className="size-3.5" aria-hidden /> Open suggestions from earlier discovery runs — newest first.
        </p>
      )}

      {!running && (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {visible.map((c) => (
            <CandidateCard
              key={c.candidate_id}
              candidate={c}
              busy={busy?.id === c.candidate_id ? (busy.force ? "force" : "investigate") : null}
              conflict={conflicts[c.candidate_id] ?? null}
              onInvestigate={() => investigate(c)}
              onForce={() => investigate(c, true)}
              onIgnore={() => ignore(c)}
            />
          ))}
        </div>
      )}

      {!running && rejected.length > 0 && <NotSuggested items={rejected} />}
    </div>
  );
}

/** "a; b; c" → ["a", "b", "c"] (the selection writes its reasons that way). */
function reasonParts(reason: string): string[] {
  return reason
    .split(/;\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function CaseFacts({ c }: { c: DiscoveryCandidate }) {
  const incident = c.incident_date || c.approximate_date;
  const people = (c.people?.length ? c.people : c.key_people) ?? [];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <span className="inline-flex items-center gap-1">
        <CalendarDays className="size-3.5" aria-hidden />
        Incident <span className="font-medium text-foreground">{incident || "unknown"}</span>
      </span>
      <span>
        Latest development <span className="font-medium text-foreground">{c.latest_development_date || "unknown"}</span>
      </span>
      {c.location && (
        <span className="inline-flex items-center gap-1">
          <MapPin className="size-3.5" aria-hidden />
          <span className="text-foreground">{c.location}</span>
        </span>
      )}
      {people.length > 0 && (
        <span className="inline-flex items-center gap-1">
          <Users className="size-3.5" aria-hidden />
          <span className="text-foreground">{people.slice(0, 4).join(", ")}</span>
        </span>
      )}
    </div>
  );
}

function StatusEvidence({ c }: { c: DiscoveryCandidate }) {
  const v = c.verification;
  const facts = (v?.key_facts ?? [])
    .map((f) => (typeof f === "string" ? { fact: f, url: undefined } : f))
    .filter((f) => f.fact);
  if (!c.resolution_evidence && !v) return null;
  return (
    <div className="rounded-md border border-border bg-subtle px-3 py-2">
      <FieldLabel>Status evidence</FieldLabel>
      {c.resolution_evidence && <p className="text-sm leading-6 text-foreground/90">{c.resolution_evidence}</p>}
      {v ? (
        <div className="mt-1 space-y-1.5 text-xs">
          <p className="text-muted-foreground">
            Verified by targeted searches
            {v.status && (
              <>
                {" "}
                — verifier says <span className="font-medium text-foreground">{humanize(v.status)}</span>
              </>
            )}
            {v.confidence != null && ` (${Math.round(v.confidence * 100)}%)`}
            {v.solved_by && v.solved_by !== "none" && ` · by ${humanize(v.solved_by)}`}
          </p>
          {v.reason && <p className="leading-5">{v.reason}</p>}
          {facts.length > 0 && (
            <ul className="list-disc space-y-0.5 pl-4">
              {facts.slice(0, 4).map((f, i) => (
                <li key={i}>
                  {f.fact}
                  {f.url && (
                    <a href={f.url} target="_blank" rel="noopener noreferrer" className="ml-1 text-primary hover:underline">
                      source
                    </a>
                  )}
                </li>
              ))}
            </ul>
          )}
          <SourceLinks sources={v.supporting_urls} max={4} />
        </div>
      ) : (
        <p className="mt-1 text-xs text-muted-foreground">Claimed by the discovery extraction — not verified yet.</p>
      )}
    </div>
  );
}

function CandidateCard({
  candidate: c,
  busy,
  conflict,
  onInvestigate,
  onForce,
  onIgnore,
}: {
  candidate: DiscoveryCandidate;
  busy: "investigate" | "force" | null;
  conflict: DuplicateConflict | null;
  onInvestigate: () => void;
  onForce: () => void;
  onIgnore: () => void;
}) {
  const languages = c.languages_available ?? [];
  const angles = c.angles ?? [];
  return (
    <Card>
      <CardHeader className="items-start gap-2">
        <div className="min-w-0 flex-1">
          <CardTitle className="flex flex-wrap items-center gap-x-2 gap-y-1 text-base leading-snug">
            <span>{c.title}</span>
            <span className="font-normal text-muted-foreground">— Status:</span>
            <ResolutionBadge status={c.resolution_status} confidence={c.resolution_confidence} size="lg" />
          </CardTitle>
        </div>
        <Badge variant={c.already_covered ? "warning" : "success"}>
          {c.already_covered ? "Already covered" : "New"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        <CaseFacts c={c} />

        {c.suggestion_reason && (
          <div>
            <FieldLabel>Why suggested</FieldLabel>
            <ul className="list-disc space-y-0.5 pl-4 text-xs leading-5 text-foreground/90">
              {reasonParts(c.suggestion_reason).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        )}

        <StatusEvidence c={c} />

        {c.rationale && (
          <div>
            <FieldLabel>Why it&apos;s interesting</FieldLabel>
            <p className="text-sm text-foreground/90">{c.rationale}</p>
          </div>
        )}
        {c.narrative_potential && (
          <div>
            <FieldLabel>Narrative potential</FieldLabel>
            <p className="text-sm text-foreground/90">{c.narrative_potential}</p>
          </div>
        )}

        {(languages.length > 0 || c.source_richness) && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
            <span className="flex items-center gap-1">
              <Globe className="size-3.5" />
              {languages.length ? languages.map((l) => LANGUAGES[l] ?? l).join(", ") : "Unknown"}
            </span>
            {c.source_richness && (
              <span>
                Source richness: <span className="font-medium text-foreground">{c.source_richness}</span>
              </span>
            )}
          </div>
        )}

        {c.already_covered && (c.matched_existing_title || c.duplicate_reason) && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            {c.matched_existing_title && <>Similar to existing case: {c.matched_existing_title}</>}
            {c.duplicate_reason && <span className="block text-muted-foreground">{c.duplicate_reason}</span>}
          </p>
        )}

        {angles.length > 0 && (
          <div>
            <FieldLabel className="mb-1">Potential angles</FieldLabel>
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-foreground/80">
              {angles.slice(0, 3).map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          </div>
        )}

        {conflict && (
          <DuplicateNotice conflict={conflict} onForce={onForce} forcing={busy === "force"} forceLabel="Investigate anyway" />
        )}

        <div className="flex gap-2 border-t border-border pt-3">
          <Button
            size="sm"
            onClick={onInvestigate}
            loading={busy === "investigate"}
            disabled={c.already_covered || busy !== null || conflict != null}
          >
            <Compass className="size-3.5" />
            Investigate
          </Button>
          <Button size="sm" variant="outline" onClick={onIgnore} disabled={busy !== null}>
            Ignore
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

const REJECTED_STATE: Record<string, { label: string; variant: "warning" | "outline" | "default" }> = {
  duplicate: { label: "Duplicate", variant: "warning" },
  filtered: { label: "Filtered", variant: "outline" },
};

/** Candidates the run did not suggest — duplicates and filtered (unsolved) — with the reason. */
function NotSuggested({ items }: { items: DiscoveryCandidate[] }) {
  return (
    <Card className="mt-6">
      <CardContent>
        <Collapsible
          title={<span className="text-sm">Not suggested — duplicates and filtered cases</span>}
          count={items.length}
        >
          <ul className="divide-y divide-border rounded-md border border-border">
            {items.map((c) => {
              const meta = REJECTED_STATE[c.state ?? ""] ?? { label: humanize(c.state ?? "rejected"), variant: "default" as const };
              const reason =
                c.state === "duplicate" ? c.duplicate_reason || c.suggestion_reason : c.suggestion_reason || c.duplicate_reason;
              return (
                <li key={c.candidate_id} className="space-y-1 px-3 py-2.5 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{c.title}</span>
                    <Badge variant={meta.variant}>{meta.label}</Badge>
                    <ResolutionBadge status={c.resolution_status} />
                    {c.created_at && <span className="text-muted-foreground">{formatDateTime(c.created_at)}</span>}
                  </div>
                  {reason && <p className="leading-5 text-foreground/90">{reason}</p>}
                  <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-muted-foreground">
                    {(c.incident_date || c.approximate_date) && <span>Incident {c.incident_date || c.approximate_date}</span>}
                    {c.location && <span>{c.location}</span>}
                    {c.duplicate_of_case_id != null && (
                      <Link href={`/cases/${c.duplicate_of_case_id}`} className="text-primary hover:underline">
                        Open matched case #{c.duplicate_of_case_id}
                      </Link>
                    )}
                    {c.duplicate_of_candidate_id != null && (
                      <span>matches earlier suggestion #{c.duplicate_of_candidate_id}</span>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </Collapsible>
      </CardContent>
    </Card>
  );
}
