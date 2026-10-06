"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Compass, Globe, Sparkles } from "lucide-react";
import { api, pollResearchJob } from "@/lib/api";
import type { DiscoveryCandidate } from "@/lib/types";
import { LANGUAGES, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/page-header";
import { EmptyState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const LANG_OPTIONS = ["en", "de", "fa", "ar", "other"];
const COUNTS = [5, 10, 20];

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

  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [ignored, setIgnored] = useState<Set<number>>(new Set());
  const [skippedDupes, setSkippedDupes] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [jobStage, setJobStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const pollAbort = useRef<AbortController | null>(null);

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
      });
      if (res.job_id != null) {
        const job = await pollResearchJob(res.job_id, {
          signal: ctl.signal,
          onUpdate: (j) =>
            setJobStage(
              j.status === "queued"
                ? "Discovery job queued…"
                : "Research is searching the web and deduplicating against your archive…",
            ),
        });
        if (job.status === "failed") {
          throw new Error(job.error || "Research provider unavailable");
        }
        setCandidates(job.result?.candidates ?? []);
        setSkippedDupes(job.result?.skipped_duplicates ?? null);
      } else {
        const result = res.result as { candidates?: DiscoveryCandidate[] } | undefined;
        setCandidates(result?.candidates ?? []);
        setSkippedDupes(null);
      }
      setIgnored(new Set());
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setRunning(false);
      setJobStage(null);
    }
  }

  async function investigate(c: DiscoveryCandidate) {
    setBusyId(c.candidate_id);
    setError(null);
    try {
      const res = await api.investigate(c.candidate_id, langs.includes("fa") ? "fa" : "en");
      router.push(`/cases/${res.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusyId(null);
    }
  }

  async function ignore(c: DiscoveryCandidate) {
    setBusyId(c.candidate_id);
    try {
      await api.ignoreCandidate(c.candidate_id);
      setIgnored((prev) => new Set(prev).add(c.candidate_id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  const visible = candidates.filter((c) => !ignored.has(c.candidate_id));

  return (
    <div>
      <PageHeader
        title="Find New Cases"
        description="Discover under-covered true-crime cases with narrative potential."
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
          </div>

          <div className="flex items-center gap-3">
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
        />
      )}

      {!running && candidates.length > 0 && visible.length === 0 && (
        <EmptyState title="All candidates handled" description="Every suggested case was investigated or ignored." />
      )}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {visible.map((c) => (
          <CandidateCard
            key={c.candidate_id}
            candidate={c}
            busy={busyId === c.candidate_id}
            onInvestigate={() => investigate(c)}
            onIgnore={() => ignore(c)}
          />
        ))}
      </div>
    </div>
  );
}

function CandidateCard({
  candidate: c,
  busy,
  onInvestigate,
  onIgnore,
}: {
  candidate: DiscoveryCandidate;
  busy: boolean;
  onInvestigate: () => void;
  onIgnore: () => void;
}) {
  return (
    <Card>
      <CardHeader className="items-start gap-2">
        <div className="min-w-0 flex-1">
          <CardTitle className="leading-snug">{c.title}</CardTitle>
        </div>
        <Badge variant={c.already_covered ? "warning" : "success"}>
          {c.already_covered ? "Already covered" : "New"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3">
        {c.rationale && (
          <div>
            <p className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Why it&apos;s interesting
            </p>
            <p className="text-sm text-foreground/90">{c.rationale}</p>
          </div>
        )}
        {c.narrative_potential && (
          <div>
            <p className="mb-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Narrative potential
            </p>
            <p className="text-sm text-foreground/90">{c.narrative_potential}</p>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Globe className="size-3.5" />
            {c.languages_available.length
              ? c.languages_available.map((l) => LANGUAGES[l] ?? l).join(", ")
              : "Unknown"}
          </span>
          <span>
            Source richness:{" "}
            <span className="font-medium text-foreground">{c.source_richness}</span>
          </span>
        </div>

        {c.already_covered && c.matched_existing_title && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            Similar to existing case: {c.matched_existing_title}
          </p>
        )}

        {c.angles.length > 0 && (
          <div>
            <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              Potential angles
            </p>
            <ul className="list-disc space-y-0.5 pl-4 text-xs text-foreground/80">
              {c.angles.slice(0, 3).map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex gap-2 border-t border-border pt-3">
          <Button size="sm" onClick={onInvestigate} loading={busy} disabled={c.already_covered}>
            <Compass className="size-3.5" />
            Investigate
          </Button>
          <Button size="sm" variant="outline" onClick={onIgnore} disabled={busy}>
            Ignore
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
