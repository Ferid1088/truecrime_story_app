"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { YoutubeChaptersOverview, YoutubeChaptersView } from "@/lib/types";
import { formatDate, isRtl, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LanguagePicker, type LanguageTabProps } from "../shared";

const STATUS = {
  ok: { label: "valid", variant: "success" },
  omitted: { label: "omitted", variant: "warning" },
  no_plan: { label: "waiting for the chapter plan", variant: "warning" },
  no_render: { label: "film not rendered", variant: "default" },
  failed: { label: "failed", variant: "danger" },
} as const;

const eur = (value: number | null | undefined) => (value == null ? "not priced" : `€${value.toFixed(4)}`);

/** One language: the chapters on the film's own timeline, how they were made and what the chapters added to the chapter writer's calls. Everything is automatic; nothing here waits for you. */
function LanguageChapters({ view, busy, onRefresh, onPlan }: { view: YoutubeChaptersView; busy: boolean; onRefresh: () => void; onPlan: () => void }) {
  const dir = isRtl(view.language) ? "rtl" : "ltr";
  const [copied, setCopied] = useState(false);
  const status = STATUS[view.status];
  const test = `chapters-${view.language}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(view.text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  const cost = view.cost;
  return (
    <Card data-testid={test}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          YouTube chapters — {langLabel(view.language)}
          <Badge variant={status.variant} data-testid={`${test}-status`}>{status.label}</Badge>
          {view.duration && <Badge variant="outline">film {view.duration}</Badge>}
          {view.chapters.length > 0 && <Badge variant="outline">{view.chapters.length} chapters</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {view.reason && <p className="text-xs text-muted-foreground" data-testid={`${test}-reason`}>{view.reason}</p>}
        {view.chapters.length > 0 && (
          <ol className="space-y-1 text-sm" data-testid={`${test}-list`}>
            {view.chapters.map((c) => (
              <li key={`${c.beat}-${c.start}`} className="flex gap-3">
                <span className="w-16 shrink-0 font-mono text-muted-foreground">{c.time}</span>
                <span dir={dir} lang={view.language}>{c.title}</span>
                {c.source === "fallback" && <Badge variant="outline">neutral title</Badge>}
              </li>
            ))}
          </ol>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={copy} disabled={view.text === ""} data-testid={`${test}-copy`}>{copied ? "Copied" : "Copy all"}</Button>
          <Button size="sm" variant="secondary" onClick={onRefresh} disabled={busy}>Refresh times</Button>
          {view.status === "no_plan" && <Button size="sm" onClick={onPlan} disabled={busy}>Write the chapters (paid)</Button>}
        </div>
        {view.dropped.length > 0 && <p className="text-xs text-muted-foreground">Adjusted: {view.dropped.join("; ")}.</p>}
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <dt>Source revision</dt>
          <dd>{view.plan_id != null ? `chapter plan #${view.plan_id}, production script #${view.production_script_id}` : "—"}{view.fingerprint ? ` · ${view.fingerprint.slice(0, 10)}` : ""}</dd>
          <dt>Generation method</dt>
          <dd>{view.method ?? "—"}{view.model ? ` · ${view.model}` : ""}</dd>
          <dt>Added AI cost</dt>
          <dd data-testid={`${test}-cost`}>
            {cost.tokens_in != null ? `≈ ${cost.tokens_in} tokens in, ${cost.tokens_out} out, ${eur(cost.cost_eur)} (estimate); ${cost.extra_llm_requests ?? 0} extra requests` : "—"}
          </dd>
          <dt>Resolved</dt>
          <dd>{view.resolved_at ? formatDate(view.resolved_at) : "—"}</dd>
        </dl>
      </CardContent>
    </Card>
  );
}

/** The YouTube chapters of the case, one tab per language: the film's own timeline, a neutral title where a title would give something away, and the list already in the film's description. */
export function ChaptersTab({ caseId, language, onLanguageChange, refreshKey }: LanguageTabProps) {
  const [data, setData] = useState<YoutubeChaptersOverview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.youtubeChapters(caseId).then((overview) => {
      if (live) {
        setData(overview);
        setError(null);
      }
    }).catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load the chapters"));
    return () => { live = false; };
  }, [caseId, refreshKey]);

  const run = async (call: () => Promise<YoutubeChaptersOverview>) => {
    setBusy(true);
    setError(null);
    try {
      setData(await call());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  const languages = data?.languages.map((l) => l.language) ?? [];
  const current = data?.languages.find((l) => l.language === language) ?? data?.languages[0];
  const valid = data?.languages.filter((l) => l.status === "ok").length ?? 0;
  return (
    <div className="space-y-4">
      {languages.length > 0 && <LanguagePicker languages={languages} value={current?.language ?? language} onChange={onLanguageChange} />}
      <Card>
        <CardHeader><CardTitle>YouTube chapters</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-muted-foreground">
            The chapter writer that already writes the film&apos;s cards picks the chapters and titles them in the same call; code finds their times in each language&apos;s finished film, keeps YouTube&apos;s rules and puts the list into the film&apos;s description. Nothing here needs your approval.
          </p>
          {data && <p data-testid="chapters-counts">{valid} of {data.languages.length} languages have valid chapters.</p>}
          {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        </CardContent>
      </Card>
      {current && <LanguageChapters key={`${current.language}-${current.fingerprint}-${current.status}`} view={current} busy={busy} onRefresh={() => run(() => api.refreshYoutubeChapters(caseId))} onPlan={() => run(() => api.planYoutubeChapters(caseId, [current.language]))} />}
    </div>
  );
}
