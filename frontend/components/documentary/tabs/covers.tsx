"use client";

import { useCallback, useEffect, useState } from "react";
import { api, apiFileUrl } from "@/lib/api";
import type { CoverCosts, CoverItem, CoversOverview, CoversSummary } from "@/lib/types";
import { langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/state";
import { LanguagePicker, type LanguageTabProps } from "../shared";

const STATUS_VARIANT = { proposed: "info", open: "warning", approved: "success", skipped: "default" } as const;
const KEY_LABEL = (key: string) => (key === "long" ? "Film cover (16:9)" : `Short ${key} cover (9:16)`);
const usd = (value: number | null | undefined) => (value == null ? "not priced" : `$${value.toFixed(4)}`);

/** One cover: its title, the scene description the image model paints, the picture, the preview and the decision. Nothing is approved before its preview has been looked at. */
function CoverCard({ caseId, language, cover, onChanged }: { caseId: number; language: string; cover: CoverItem; onChanged: () => void }) {
  const [title, setTitle] = useState(cover.title);
  const [scene, setScene] = useState(cover.scene_prompt);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const rtl = language === "fa" || language === "ar";
  const shown = cover.status === "approved" ? cover.approved_url : cover.preview_url;

  const run = async (what: string, call: () => Promise<CoverItem>) => {
    setBusy(what);
    setError(null);
    try {
      const result = await call();
      setProblems(result.problems ?? []);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(null);
    }
  };
  const working = busy != null;

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
          <Button size="sm" variant="secondary" disabled={working || title.trim() === cover.title} onClick={() => run("title", () => api.changeCover(caseId, language, cover.key, { title }))}>
            Save title
          </Button>
        </div>
        <div className="space-y-2">
          <Textarea value={scene} onChange={(e) => setScene(e.target.value)} rows={2} aria-label={`Scene of ${KEY_LABEL(cover.key)}`} />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="secondary" disabled={working || scene.trim() === cover.scene_prompt} onClick={() => run("scene", () => api.changeCover(caseId, language, cover.key, { scene }))}>
              Save scene
            </Button>
            <span className="text-xs text-muted-foreground">The image model paints this scene (no people, no weapons). A new scene needs a new picture.</span>
          </div>
        </div>
        {cover.reason && <p dir="auto" className="text-xs text-muted-foreground">Why this scene: {cover.reason}</p>}
        {(cover.validation.issues ?? []).length > 0 && <p className="text-xs text-rose-600">Checks: {(cover.validation.issues ?? []).map((i) => i.type).join(", ")}</p>}

        <div className="flex flex-wrap items-center gap-3">
          {cover.image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={apiFileUrl(cover.image.url)} alt={`Scene of ${KEY_LABEL(cover.key)}`} className="h-28 rounded border" />
          ) : (
            <span className="text-xs text-muted-foreground">No picture yet.</span>
          )}
          {cover.image && (
            <span className="text-xs text-muted-foreground" data-testid={`cover-${cover.key}-cost`}>
              {usd(cover.image.cost_usd)} estimated · {cover.image.image_tokens_out} image tokens · {cover.image.duration_s} s · attempt {cover.image.attempt}
            </span>
          )}
          <Button size="sm" variant="secondary" disabled={working || !cover.scene_prompt} onClick={() => run("image", () => api.makeCoverImage(caseId, language, cover.key, cover.image != null))}>
            {busy === "image" ? "Painting…" : cover.image ? "Make the picture again (paid)" : "Make the picture (paid)"}
          </Button>
        </div>

        {shown && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={apiFileUrl(shown)} alt={`Preview of ${KEY_LABEL(cover.key)}`} className={cover.kind === "long" ? "w-full max-w-xl rounded border" : "h-80 rounded border"} />
        )}
        {problems.map((p) => <p key={p} className="text-xs text-rose-600">{p}</p>)}
        {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}

        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" disabled={working || !cover.image || !cover.title} onClick={() => run("preview", () => api.previewCover(caseId, language, cover.key))}>Preview</Button>
          <Button size="sm" disabled={working || cover.status === "approved" || !cover.preview_url} onClick={() => run("approve", () => api.approveCover(caseId, language, cover.key))}>Approve</Button>
          <Button size="sm" variant="ghost" disabled={working || cover.status === "skipped"} onClick={() => run("skip", () => api.skipCover(caseId, language, cover.key))}>Skip</Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** What the pictures of a language cost so far, from the attempts the app recorded (an estimate: the gateway reports tokens but no price). */
function CostBook({ costs }: { costs: CoverCosts }) {
  return (
    <details className="text-xs" data-testid="cover-costs">
      <summary className="cursor-pointer">
        Cost book: {costs.pictures} pictures, {usd(costs.cost_usd)} (≈ €{costs.cost_eur.toFixed(4)}), {costs.image_tokens_out} image tokens, {costs.seconds} s, {costs.failed} failed attempts
      </summary>
      <p className="mt-1 text-muted-foreground">
        Model {costs.model}. Estimated from the output image tokens at the configured rate ({costs.rate_status}); input tokens are recorded but not priced.
      </p>
      <table className="mt-1 w-full text-left">
        <thead>
          <tr><th>Cover</th><th>Try</th><th>Result</th><th>Text in</th><th>Image in</th><th>Image out</th><th>USD</th><th>Seconds</th></tr>
        </thead>
        <tbody>
          {costs.rows.map((r) => (
            <tr key={r.id}>
              <td>{r.cover_key}</td><td>{r.attempt}</td><td>{r.ok ? "ok" : (r.error ?? "failed").slice(0, 40)}</td><td>{r.text_tokens_in ?? "–"}</td><td>{r.image_tokens_in ?? "–"}</td>
              <td>{r.image_tokens_out ?? "–"}</td><td>{usd(r.cost_usd)}</td><td>{r.duration_s}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** The cover review: for each language the film's cover and one cover for each short, with the planner's title and scene to change, the picture the image model paints, preview, approve or skip. Only approved covers go into the upload plan. */
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
            A film cover and one cover for each short. The planner proposes a title and a scene; the image model paints the scene when you press the button (it costs); the logo, title and avatar are added in code. Nothing is approved until you do it. Only approved covers go into the upload plan.
          </p>
          {mine && summary && (
            <p data-testid="covers-counts">
              {langLabel(language)}: {mine.approved} approved, {mine.proposed} proposed, {mine.open} open, {mine.skipped} skipped, {mine.missing} not planned yet (of {summary.expected_per_language}).
              All languages: {total} approved.
            </p>
          )}
          {data && <CostBook costs={data.costs} />}
          {data && <p className="text-xs text-amber-700 dark:text-amber-400">Platform cover ways and safe zones are still marked &quot;to test&quot; ({data.safe_zones}): no real upload has confirmed them.</p>}
          <Button size="sm" onClick={propose} disabled={busy}>{busy ? "Planning…" : data && data.covers.length > 0 ? "Plan again" : "Plan the covers"}</Button>
          {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        </CardContent>
      </Card>
      {data && data.covers.length === 0 ? (
        <EmptyState title="No covers planned yet" description="Plan the covers once this language has its short clips." />
      ) : (
        data?.covers.map((cover) => <CoverCard key={`${cover.key}-${cover.id}`} caseId={caseId} language={language} cover={cover} onChanged={load} />)
      )}
    </div>
  );
}
