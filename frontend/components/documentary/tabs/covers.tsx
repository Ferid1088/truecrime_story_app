"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiFileUrl } from "@/lib/api";
import type { CoverCandidate, CoverItem, CoversOverview, CoversSummary } from "@/lib/types";
import { langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/state";
import { LanguagePicker, type LanguageTabProps } from "../shared";

const STATUS_VARIANT = { proposed: "info", open: "warning", approved: "success", skipped: "default" } as const;
const KEY_LABEL = (key: string) => (key === "long" ? "Film cover (16:9)" : `Short ${key} cover (9:16)`);

/** One cover: its title, its scene among the film's pictures, the preview, and the decision. Nothing is approved before its preview has been looked at. */
function CoverCard({ caseId, language, cover, candidates, onChanged }: { caseId: number; language: string; cover: CoverItem; candidates: CoverCandidate[]; onChanged: () => void }) {
  const [title, setTitle] = useState(cover.title);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const rtl = language === "fa" || language === "ar";
  const shown = cover.status === "approved" ? cover.approved_url : cover.preview_url;
  const suggested = cover.validation.suggested_scene;

  const run = async (call: () => Promise<CoverItem>) => {
    setBusy(true);
    setError(null);
    try {
      const result = await call();
      setProblems(result.problems ?? []);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card data-testid={`cover-${cover.key}`}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          {KEY_LABEL(cover.key)}
          <Badge variant={STATUS_VARIANT[cover.status]} data-testid={`cover-${cover.key}-status`}>{cover.status}</Badge>
          <Badge variant="outline">{cover.source === "owner" ? "your choice" : "proposed"} · v{cover.version}</Badge>
          {cover.confidence != null && <Badge variant="outline">certainty {Math.round(cover.confidence * 100)}%</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-2">
          <Input value={title} dir={rtl ? "rtl" : "ltr"} onChange={(e) => setTitle(e.target.value)} aria-label={`Title of ${KEY_LABEL(cover.key)}`} className="max-w-sm" />
          <Button size="sm" variant="secondary" disabled={busy || title.trim() === cover.title} onClick={() => run(() => api.changeCover(caseId, language, cover.key, { title }))}>
            Save title
          </Button>
        </div>
        {cover.reason && <p dir="auto" className="text-xs text-muted-foreground">Why this picture: {cover.reason}</p>}
        {suggested && cover.scene_code == null && <p className="text-xs text-amber-700 dark:text-amber-400">The planner was unsure about {suggested}: choose a scene yourself.</p>}
        {(cover.validation.issues ?? []).length > 0 && <p className="text-xs text-rose-600">Checks: {(cover.validation.issues ?? []).map((i) => i.type).join(", ")}</p>}

        <div role="radiogroup" aria-label="Scene" className="flex flex-wrap gap-2">
          {candidates.map((c) => (
            <button
              key={c.code}
              type="button"
              role="radio"
              aria-checked={cover.scene_code === c.code}
              aria-label={`Scene ${c.code}: ${c.what}`}
              title={c.what}
              disabled={busy}
              onClick={() => run(() => api.changeCover(caseId, language, cover.key, { scene_code: c.code }))}
              className={`h-16 w-24 overflow-hidden rounded border-2 ${cover.scene_code === c.code ? "border-indigo-500" : "border-transparent"}`}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={apiFileUrl(c.image_url)} alt={c.what} className="h-full w-full object-cover" />
            </button>
          ))}
          {candidates.length === 0 && <span className="text-xs text-muted-foreground">No picture of this film can be a scene.</span>}
          {cover.scene_code != null && (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => run(() => api.changeCover(caseId, language, cover.key, { clear_scene: true }))}>No scene</Button>
          )}
        </div>

        {shown && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={apiFileUrl(shown)} alt={`Preview of ${KEY_LABEL(cover.key)}`} className={cover.kind === "long" ? "w-full max-w-xl rounded border" : "h-80 rounded border"} />
        )}
        {problems.map((p) => <p key={p} className="text-xs text-rose-600">{p}</p>)}
        {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}

        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" disabled={busy || !cover.scene_code || !cover.title} onClick={() => run(() => api.previewCover(caseId, language, cover.key))}>Preview</Button>
          <Button size="sm" disabled={busy || cover.status === "approved" || !cover.preview_url} onClick={() => run(() => api.approveCover(caseId, language, cover.key))}>Approve</Button>
          <Button size="sm" variant="ghost" disabled={busy || cover.status === "skipped"} onClick={() => run(() => api.skipCover(caseId, language, cover.key))}>Skip</Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** The cover review: for each language the film's cover and one cover for each short, with the planner's title and scene to change, preview, approve or skip. Only approved covers go into the upload plan. */
export function CoversTab({ caseId, settings, language, onLanguageChange, refreshKey }: LanguageTabProps) {
  const [data, setData] = useState<CoversOverview | null>(null);
  const [summary, setSummary] = useState<CoversSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [overview, counts] = await Promise.all([api.covers(caseId, language), api.coversSummary(caseId)]);
      setData(overview);
      setSummary(counts);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load the covers");
    }
  }, [caseId, language]);

  useEffect(() => { void load(); }, [load, refreshKey]);

  const propose = async () => {
    setBusy(true);
    setError(null);
    try {
      setData(await api.proposeCovers(caseId, language));
      setSummary(await api.coversSummary(caseId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "The planner failed");
    } finally {
      setBusy(false);
    }
  };

  const mine = summary?.languages[language];
  const total = summary ? Object.values(summary.languages).reduce((n, l) => n + l.approved, 0) : 0;
  return (
    <div className="space-y-4">
      <LanguagePicker languages={settings.languages} value={language} onChange={onLanguageChange} />
      <Card>
        <CardHeader>
          <CardTitle>Covers — {langLabel(language)}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-muted-foreground">
            A film cover and one cover for each short. The planner proposes a title and a picture of the film; nothing is approved until you do it. Only approved covers go into the upload plan.
          </p>
          {mine && summary && (
            <p data-testid="covers-counts">
              {langLabel(language)}: {mine.approved} approved, {mine.proposed} proposed, {mine.open} open, {mine.skipped} skipped, {mine.missing} not planned yet (of {summary.expected_per_language}).
              All languages: {total} approved.
            </p>
          )}
          {data && <p className="text-xs text-amber-700 dark:text-amber-400">Platform cover ways and safe zones are still marked &quot;to test&quot; ({data.safe_zones}): no real upload has confirmed them.</p>}
          <Button size="sm" onClick={propose} disabled={busy}>{busy ? "Planning…" : data && data.covers.length > 0 ? "Plan again" : "Plan the covers"}</Button>
          {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        </CardContent>
      </Card>
      {data && data.covers.length === 0 ? (
        <EmptyState title="No covers planned yet" description="Plan the covers once this language has its short clips." />
      ) : (
        data?.covers.map((cover) => <CoverCard key={`${cover.key}-${cover.id}`} caseId={caseId} language={language} cover={cover} candidates={data.candidates} onChanged={load} />)
      )}
    </div>
  );
}
