"use client";

import { useEffect, useState } from "react";
import { api, apiFileUrl } from "@/lib/api";
import type { FilmTitles, NamingLanguage, TitlesOverview } from "@/lib/types";
import { isRtl, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { LanguagePicker, type LanguageTabProps } from "../shared";

/** One language: the title the film has, the other two options, the idea behind them, the film cover it is paired with and the checks. Chosen automatically; you may replace it. */
function LanguageTitles({ caseId, view, onChanged }: { caseId: number; view: FilmTitles; onChanged: () => void }) {
  const rtl = isRtl(view.language);
  const dir = rtl ? "rtl" : "ltr";
  const [mine, setMine] = useState(view.title ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const test = `titles-${view.language}`;
  const working = busy != null;

  const run = async (what: string, call: () => Promise<unknown>) => {
    setBusy(what);
    setError(null);
    try {
      await call();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(null);
    }
  };
  const language = view.language as NamingLanguage;
  const copy = async () => {
    if (!view.title) return;
    try {
      await navigator.clipboard.writeText(view.title);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  const alternatives = view.options.filter((o) => !o.selected);

  return (
    <div className="space-y-4" data-testid={test}>
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
            Title — {langLabel(view.language)}
            {view.source === "auto" && <Badge variant="info" data-testid={`${test}-source`}>chosen by the app</Badge>}
            {view.source === "owner" && <Badge variant="success" data-testid={`${test}-source`}>your title</Badge>}
            {!view.title && <Badge variant="default">no title yet</Badge>}
            {view.published && <Badge variant="outline">published</Badge>}
            <Badge variant={view.validation.ok ? "success" : "warning"} data-testid={`${test}-validation`}>{view.validation.ok ? "valid" : "needs a look"}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {view.title && <p dir={dir} lang={view.language} className="text-lg font-medium" data-testid={`${test}-title`}>{view.title}</p>}
          {view.youtube_title && <p dir={dir} lang={view.language} className="text-xs text-muted-foreground">On YouTube: {view.youtube_title}</p>}
          {view.hook && <p className="text-xs text-muted-foreground">The idea (verified): {view.hook}</p>}
          {view.validation.issues.length > 0 && (
            <ul className="space-y-0.5 text-xs text-amber-700 dark:text-amber-400">{view.validation.issues.map((i) => <li key={i}>{i}</li>)}</ul>
          )}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={copy} disabled={!view.title} data-testid={`${test}-copy`}>{copied ? "Copied" : "Copy"}</Button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Input value={mine} dir={dir} lang={view.language} className="max-w-sm" aria-label={`Your title in ${langLabel(view.language)}`} onChange={(e) => setMine(e.target.value)} disabled={view.published} />
            <Button size="sm" disabled={working || view.published || mine.trim() === "" || mine.trim() === view.title}
              onClick={() => run("mine", async () => { const row = await api.manualTitle(caseId, language, mine.trim()); await api.approveTitle(caseId, row.id); })}>
              {busy === "mine" ? "Checking…" : "Use my title"}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">Your title goes through the same checks (spoilers, accusations, collisions) as the generated ones, and the app never replaces it.</p>
          {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">Alternatives</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {alternatives.length === 0 && <p className="text-xs text-muted-foreground">No other option yet.</p>}
          {alternatives.map((o) => (
            <div key={o.id} className="flex flex-wrap items-center gap-2" data-testid={`${test}-option-${o.id}`}>
              {o.angle && <Badge variant="outline">{o.angle}</Badge>}
              <span dir={dir} lang={view.language}>{o.title}</span>
              <Button size="sm" variant="secondary" disabled={working || view.published} onClick={() => run(`use-${o.id}`, () => api.approveTitle(caseId, o.candidate_id))}>Use</Button>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">Film cover</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          {view.cover ? (
            <>
              {(view.cover.approved_url ?? view.cover.preview_url) ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={apiFileUrl((view.cover.approved_url ?? view.cover.preview_url) as string)} alt={`Film cover in ${langLabel(view.language)}`} className="w-full max-w-xl rounded border" />
              ) : <p className="text-xs text-muted-foreground">No preview yet.</p>}
              <p className="text-xs text-muted-foreground">Thumbnail text: <span dir={dir} lang={view.language}>{view.cover.title}</span> · cover {view.cover.status}</p>
              {view.pairing && <p className="text-xs text-muted-foreground" data-testid={`${test}-pairing`}>Paired with option {view.pairing.pick} ({view.pairing.angle}): {view.pairing.reason}</p>}
            </>
          ) : <p className="text-xs text-muted-foreground">The cover is not planned yet (Film cover tab).</p>}
        </CardContent>
      </Card>
    </div>
  );
}

/** The film titles of the case, one tab per language: chosen by the app from the title writer's options and the cover planner's pairing, with your own title always first. */
export function TitlesTab({ caseId, language, onLanguageChange, refreshKey }: LanguageTabProps) {
  const [data, setData] = useState<TitlesOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    api.filmTitles(caseId).then((overview) => {
      if (live) {
        setData(overview);
        setError(null);
      }
    }).catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load the titles"));
    return () => { live = false; };
  }, [caseId, refreshKey, tick]);

  const languages = data?.languages.map((l) => l.language) ?? [];
  const current = data?.languages.find((l) => l.language === language) ?? data?.languages[0];
  return (
    <div className="space-y-4">
      {languages.length > 0 && <LanguagePicker languages={languages} value={current?.language ?? language} onChange={onLanguageChange} />}
      <Card>
        <CardHeader><CardTitle>Titles</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-muted-foreground">
            The title writer proposes titles that say what happened, each built on a different angle, in each language; the cover planner says which one its scene and thumbnail text complement best; the app takes that one when it ranks close to the best, else the best. Nothing here needs your approval: replace a title any time.
          </p>
          {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        </CardContent>
      </Card>
      {current && <LanguageTitles key={`${current.language}-${current.title}-${current.source}`} caseId={caseId} view={current} onChanged={() => setTick((n) => n + 1)} />}
    </div>
  );
}
