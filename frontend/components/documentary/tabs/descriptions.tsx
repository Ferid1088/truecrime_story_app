"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DescriptionsOverview, FilmDescription } from "@/lib/types";
import { formatDate, isRtl, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { LanguagePicker, type LanguageTabProps } from "../shared";

const STATUS = {
  ok: { label: "ready", variant: "success" },
  no_plan: { label: "no description written yet", variant: "warning" },
  invalid: { label: "failed a check: plain description used", variant: "warning" },
  failed: { label: "failed", variant: "danger" },
  no_render: { label: "film not rendered", variant: "default" },
} as const;

const eur = (value: number | null | undefined) => (value == null ? "not priced" : `€${value.toFixed(4)}`);

/** One language: the whole YouTube description ready to copy, your own edits (kept, never replaced by the app) and how it was made. */
function LanguageDescription({ caseId, view, onChanged }: { caseId: number; view: FilmDescription; onChanged: (data: DescriptionsOverview) => void }) {
  const dir = isRtl(view.language) ? "rtl" : "ltr";
  const [text, setText] = useState(view.text);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const test = `description-${view.language}`;
  const status = STATUS[view.status];
  const dirty = text !== view.text;
  const errors = view.issues.filter((i) => i.severity === "error");
  const working = busy != null;

  const run = async (what: string, call: () => Promise<DescriptionsOverview>) => {
    setBusy(what);
    setError(null);
    try {
      onChanged(await call());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(null);
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Copying is not allowed here; select the text and copy it by hand.");
    }
  };

  return (
    <Card data-testid={test}>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
          YouTube description — {langLabel(view.language)}
          <Badge variant={status.variant} data-testid={`${test}-status`}>{status.label}</Badge>
          {view.edited && <Badge variant="success" data-testid={`${test}-edited`}>your text</Badge>}
          {view.status === "ok" && !view.edited && <Badge variant="info">automatic</Badge>}
          {view.status === "ok" && <Badge variant={errors.length > 0 ? "danger" : view.issues.length > 0 ? "warning" : "success"} data-testid={`${test}-validation`}>{errors.length > 0 ? "errors" : view.issues.length > 0 ? "valid, needs a look" : "valid"}</Badge>}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {view.reason && <p className="text-xs text-muted-foreground" data-testid={`${test}-reason`}>{view.reason}</p>}
        <Textarea value={text} dir={dir} lang={view.language} rows={16} aria-label={`YouTube description in ${langLabel(view.language)}`} data-testid={`${test}-field`} onChange={(e) => setText(e.target.value)} placeholder="The description appears when the film is rendered and its chapter plan has the description." />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <span className={text.length > view.max_chars ? "font-medium text-rose-600" : undefined} data-testid={`${test}-chars`}>{text.length} / {view.max_chars} characters</span>
          {view.resolved_at && <span>Built: {formatDate(view.resolved_at)}{view.model ? ` · ${view.model}` : ""}</span>}
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={copy} disabled={text.trim() === ""} data-testid={`${test}-copy`}>{copied ? "Copied" : "Copy all"}</Button>
          <Button size="sm" disabled={working || !dirty || text.length > view.max_chars} onClick={() => run("save", () => api.saveFilmDescription(caseId, view.language, text))} data-testid={`${test}-save`}>Save my text</Button>
          {view.edited && <Button size="sm" variant="secondary" disabled={working} onClick={() => run("reset", () => api.saveFilmDescription(caseId, view.language, null))}>Use the automatic text</Button>}
          <Button size="sm" variant="secondary" disabled={working || view.status === "no_render"} onClick={() => run("regenerate", () => api.regenerateFilmDescription(caseId, view.language))} data-testid={`${test}-regenerate`}>{busy === "regenerate" ? "Writing…" : "Regenerate (paid)"}</Button>
        </div>
        {view.edited && view.auto_text && view.auto_text !== view.text && <p className="text-xs text-muted-foreground">Your text is in use. The automatic text differs; “Use the automatic text” switches to it.</p>}
        {view.dropped.length > 0 && <p className="text-xs text-muted-foreground">Left out to fit YouTube&apos;s limit: {view.dropped.join(", ")}.</p>}
        {view.issues.length > 0 && (
          <ul className="space-y-0.5 text-xs" data-testid={`${test}-issues`}>
            {view.issues.map((i, n) => <li key={`${i.type}-${n}`} className={i.severity === "error" ? "text-rose-600" : "text-amber-700 dark:text-amber-400"}>{i.severity === "error" ? "Error" : "Check"}: {i.message}</li>)}
          </ul>
        )}
        {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        <p className="text-xs text-muted-foreground" data-testid={`${test}-cost`}>
          Added AI cost (chapters and description together): {view.cost.tokens_in != null ? `≈ ${view.cost.tokens_in} tokens in, ${view.cost.tokens_out} out, ${eur(view.cost.cost_eur)} (estimate); ${view.cost.extra_llm_requests ?? 0} extra requests` : "—"}
        </p>
      </CardContent>
    </Card>
  );
}

/** The YouTube descriptions of the case, one tab per language: the narrative the chapter writer wrote with the chapters, disclosure, hashtags and channel around it. Automatic; your own text always wins. */
export function DescriptionsTab({ caseId, language, onLanguageChange, refreshKey }: LanguageTabProps) {
  const [data, setData] = useState<DescriptionsOverview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.filmDescriptions(caseId).then((overview) => {
      if (live) {
        setData(overview);
        setError(null);
      }
    }).catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load the descriptions"));
    return () => { live = false; };
  }, [caseId, refreshKey]);

  const languages = data?.languages.map((l) => l.language) ?? [];
  const current = data?.languages.find((l) => l.language === language) ?? data?.languages[0];
  const ready = data?.languages.filter((l) => l.status === "ok" || l.edited).length ?? 0;
  return (
    <div className="space-y-4">
      {languages.length > 0 && <LanguagePicker languages={languages} value={current?.language ?? language} onChange={onLanguageChange} />}
      <Card>
        <CardHeader><CardTitle>Descriptions</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-muted-foreground">
            The chapter writer writes the hook and overview in its own call, from the first part of the story only; code adds the chapters, the AI disclosure, hashtags and the channel. A description holds no references: no sources, no links, no picture credits. Nothing needs your approval: edit the text and the app keeps it.
          </p>
          {data && <p data-testid="descriptions-counts">{ready} of {data.languages.length} languages have a description.</p>}
          {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        </CardContent>
      </Card>
      {current && <LanguageDescription key={`${current.language}-${current.resolved_at}-${current.edited}-${current.text.length}`} caseId={caseId} view={current} onChanged={setData} />}
    </div>
  );
}
