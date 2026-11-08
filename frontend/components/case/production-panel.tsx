"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Rocket, Save } from "lucide-react";
import { ApiError, autopilotApi, type CaseProduction } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { LANGUAGES, humanize } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { OutfitChoice } from "@/components/autopilot/outfit-choice";

const POLL_MS = 5000;
const ACTIVE = ["running", "waiting_owner"];
const RUN_VARIANT = { running: "info", waiting_owner: "warning", completed: "success", cancelled: "outline" } as const;

/** Production settings of the case: the languages to make, the host outfit of each, and the Autopilot button. Nothing starts
 * before the case has an approved name; a language that is not ticked and saved is never produced. */
export function ProductionPanel({ caseId }: { caseId: number }) {
  const production = useApi(() => autopilotApi.production(caseId), [caseId], { keepPrevious: true });
  const data = production.data;
  const run = data?.run ?? null;
  const active = run != null && ACTIVE.includes(run.status);
  const [langs, setLangs] = useState<string[] | null>(null);
  const [outfits, setOutfits] = useState<Record<string, string>>({});
  const [photos, setPhotos] = useState<Record<string, number[]>>({});
  const [busy, setBusy] = useState<"save" | "start" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(production.refetch, POLL_MS);
    return () => clearInterval(timer);
  }, [active, production.refetch]);

  if (!data) return <p className="text-sm text-muted-foreground">{production.error ?? "Loading the production settings…"}</p>;

  const chosen = langs ?? data.languages;
  const selectedOutfit = (language: string) =>
    outfits[language] ?? data.outfits.find((row) => row.language === language)?.selected ?? "";
  const photoIds = (language: string) => {
    if (photos[language]) return photos[language];
    const option = data.outfits.find((row) => row.language === language)?.options.find((o) => o.key === selectedOutfit(language));
    return (option?.photos ?? []).filter((p) => p.selected && p.status === "available").map((p) => p.id);
  };
  const dirty = !data.saved || chosen.join(",") !== data.languages.join(",") || Object.keys(outfits).length > 0 || Object.keys(photos).length > 0;
  const locked = active || busy !== null;

  function toggle(language: string, on: boolean) {
    if (!data) return;
    setLangs(data.allowed_languages.filter((l) => (l === language ? on : chosen.includes(l))));
  }

  async function save() {
    setBusy("save");
    setError(null);
    try {
      const chosenOutfits = Object.fromEntries(chosen.map((l) => [l, selectedOutfit(l)]).filter(([, key]) => key));
      const chosenPhotos = Object.fromEntries(chosen.map((l) => [l, photoIds(l)]).filter(([l]) => chosenOutfits[l as string]));
      await autopilotApi.saveProduction(caseId, chosen, chosenOutfits, chosenPhotos);
      setLangs(null);
      setOutfits({});
      setPhotos({});
      production.refetch();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function start() {
    setBusy("start");
    setError(null);
    try {
      await autopilotApi.startCaseAutopilot(caseId);
      production.refetch();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card data-testid="production-panel">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle>Production</CardTitle>
        <div className="flex items-center gap-2">
          <Badge variant={data.name.approved ? "success" : "warning"} data-testid="production-name-status">
            {data.name.approved ? "Name approved" : "Name not approved"}
          </Badge>
          {run && <Badge variant={RUN_VARIANT[run.status]} data-testid="production-run-status">{humanize(run.status)}</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-xs text-muted-foreground">
          English is made first, with its research, pictures and documents; you review it and its pictures. After you approve the English
          film every ticked language starts at once. Persian has its own review that holds no other language back. A language that is
          not ticked is never produced.
        </p>
        <fieldset disabled={locked}>
          <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Languages to produce</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {data.allowed_languages.map((language) => (
              <Checkbox
                key={language}
                label={LANGUAGES[language] ?? language}
                checked={chosen.includes(language)}
                disabled={locked || language === data.required_language}
                onChange={(event) => toggle(language, event.target.checked)}
                data-testid={`production-language-${language}`}
              />
            ))}
          </div>
        </fieldset>
        <fieldset className="space-y-2" disabled={locked}>
          <legend className="text-xs font-medium text-muted-foreground">Host outfit per language</legend>
          {data.outfits.filter((row) => chosen.includes(row.language)).map((row) => (
            <OutfitChoice
              key={row.language}
              row={row}
              value={selectedOutfit(row.language)}
              photoIds={photoIds(row.language)}
              disabled={locked}
              onOutfit={(key) => {
                setOutfits((current) => ({ ...current, [row.language]: key }));
                setPhotos((current) => ({ ...current, [row.language]: [] }));
              }}
              onPhotos={(ids) => setPhotos((current) => ({ ...current, [row.language]: ids }))}
              onAdded={production.refetch}
              testId={`production-outfit-${row.language}`}
            />
          ))}
        </fieldset>
        {data.estimate && !dirty && (
          <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs" data-testid="production-estimate">
            Estimated cost for the saved languages: <span className="font-semibold">€{data.estimate.estimated_eur.toFixed(2)}</span>{" "}
            (range €{data.estimate.range_eur.low.toFixed(2)}–€{data.estimate.range_eur.high.toFixed(2)}; picture research €
            {data.estimate.visual_research_eur.toFixed(2)}). The only limit is the actual spend: it stops before a paid call once
            €{data.estimate.cap_eur.toFixed(2)} is spent. Publishing is a dry run.
          </p>
        )}
        {!data.name.approved && (
          <p className="text-xs text-amber-600" data-testid="production-name-hint">
            Approve the English name in the Naming tab first: nothing is produced before the case is confirmed.
          </p>
        )}
        {!dirty && data.blockers.length > 0 && (
          <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground" data-testid="production-blockers">
            {data.blockers.map((b) => <li key={b}>{b}</li>)}
          </ul>
        )}
        {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        <div className="flex flex-wrap items-center justify-end gap-2">
          <Button size="sm" variant="outline" onClick={save} loading={busy === "save"} disabled={locked || !dirty} data-testid="production-save">
            <Save className="size-3.5" />
            Save settings
          </Button>
          <Button size="sm" onClick={start} loading={busy === "start"} disabled={locked || dirty || data.blockers.length > 0}
                  data-testid="production-autopilot">
            <Rocket className="size-3.5" />
            Autopilot
          </Button>
        </div>
        <RunStatus production={data} />
      </CardContent>
    </Card>
  );
}

function RunStatus({ production }: { production: CaseProduction }) {
  const run = production.run;
  if (!run) return null;
  const blocking = run.needs_you.filter((n) => n.blocking);
  return (
    <div className="space-y-2 border-t border-border pt-3" data-testid="production-run">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium">Production status</span>
        <Link href={`/autopilot/${run.id}`} className="text-xs text-primary hover:underline">Open the run page</Link>
      </div>
      <ol className="space-y-0.5 text-xs">
        {run.steps.map((step) => (
          <li key={step.name} className="flex justify-between gap-2">
            <span>{humanize(step.name)}</span>
            <span className="text-muted-foreground">{step.status}{step.detail ? ` · ${step.detail}` : ""}</span>
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap gap-1.5 text-xs">
        {Object.entries(run.languages).map(([language, view]) => (
          <Badge key={language} variant="outline" data-testid={`production-film-${language}`}>
            {LANGUAGES[language] ?? language}: {view.film_job ? view.film_job.status : "not started"}
          </Badge>
        ))}
      </div>
      {blocking.length > 0 && (
        <ul className="space-y-1 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs" data-testid="production-needs">
          {blocking.map((n) => <li key={n.key}><span className="font-medium">{n.language ? `${n.language}: ` : ""}</span>{n.message}</li>)}
        </ul>
      )}
    </div>
  );
}
