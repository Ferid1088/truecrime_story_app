"use client";

import { useState } from "react";
import { ApiError, mvpApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { EngineBeat, MvpEngines } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Statement = { path: (string | number)[]; text: string; basis?: string };

/** Every statement of an engine's data (a text with its kind of statement), so the owner can edit the words. */
function statements(node: unknown, path: (string | number)[] = []): Statement[] {
  if (Array.isArray(node)) return node.flatMap((v, i) => statements(v, [...path, i]));
  if (node && typeof node === "object") {
    const obj = node as Record<string, unknown>;
    const own = typeof obj.text === "string" ? [{ path: [...path, "text"], text: obj.text, basis: obj.basis as string | undefined }] : [];
    return [...own, ...Object.entries(obj).filter(([, v]) => v && typeof v === "object").flatMap(([k, v]) => statements(v, [...path, k]))];
  }
  return [];
}

function withText(data: unknown, path: (string | number)[], text: string): unknown {
  const copy = JSON.parse(JSON.stringify(data));
  let at = copy;
  path.slice(0, -1).forEach((k) => { at = at[k]; });
  at[path[path.length - 1]] = text;
  return copy;
}

function BeatRow({ caseId, beat, engines, onChanged }: { caseId: number; beat: EngineBeat; engines: MvpEngines["engines"]; onChanged: (v: MvpEngines) => void }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [draft, setDraft] = useState<unknown>(beat.data);
  const [seconds, setSeconds] = useState<string>(String(beat.seconds ?? beat.default_seconds ?? ""));
  const name = engines.find((e) => e.kind === beat.engine)?.name ?? beat.engine;

  async function act(body: { action: string; engine?: string; data?: unknown; seconds?: number }, done: string) {
    setBusy(true);
    setNote(null);
    try {
      onChanged(await mvpApi.engineOverride(caseId, { beat: beat.beat, ...body }));
      setNote(done);
    } catch (e) {
      setNote(e instanceof ApiError ? e.message : "Could not save.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border p-3 text-sm" data-testid={`engine-beat-${beat.beat}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs">{beat.beat}</span>
        {beat.engine ? <Badge>{name}</Badge> : <Badge variant="outline">no engine</Badge>}
        {beat.on_screen && <Badge variant="success">in the film</Badge>}
        {beat.disabled && <Badge variant="outline">off</Badge>}
        {beat.edited && <Badge variant="outline">edited</Badge>}
        {beat.forced && <Badge variant="outline">chosen by you</Badge>}
        <span className="text-muted-foreground">{beat.why}</span>
        <Button size="sm" variant="ghost" className="ml-auto" onClick={() => setOpen((v) => !v)}>{open ? "Close" : "Inspect"}</Button>
      </div>
      {open && (
        <div className="mt-3 space-y-3">
          {beat.problems.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-rose-700" data-testid="engine-problems">
              {beat.problems.map((p) => <li key={p}>{p}</li>)}
            </ul>
          )}
          {beat.missing && <p className="text-xs text-amber-700">Missing evidence: {beat.missing}</p>}
          {beat.engine && beat.steps.length > 0 && (
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {beat.steps.map((s, i) => (
                // eslint-disable-next-line @next/next/no-img-element
                <img key={s} src={mvpApi.enginePreview(caseId, beat.beat, i + 1)} alt={`${name} step ${i + 1}`} className="w-full rounded border" loading="lazy" />
              ))}
            </div>
          )}
          {beat.provenance.length > 0 && (
            <div className="space-y-1 text-xs" data-testid="engine-provenance">
              <p className="font-medium">Sources</p>
              {beat.provenance.map((p) => (
                <p key={p.fact_id} className="text-muted-foreground">
                  <span className="font-mono">{p.fact_id}</span>{p.disputed ? " (disputed)" : ""}: {p.text}
                  {p.source_ids.length > 0 && ` · sources ${p.source_ids.join(", ")}`}
                </p>
              ))}
            </div>
          )}
          {beat.engine && (
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => act({ action: beat.disabled ? "enable" : "disable" }, beat.disabled ? "Switched on." : "Switched off.")}>
                {beat.disabled ? "Switch on" : "Switch off"}
              </Button>
              <label className="flex items-center gap-1 text-xs">
                Length (s)
                <input className="w-16 rounded border px-1 py-0.5" value={seconds} onChange={(e) => setSeconds(e.target.value)} />
              </label>
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => act({ action: "timing", seconds: Number(seconds) }, "Length saved.")}>Set length</Button>
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <select className="rounded border px-2 py-1 text-xs" defaultValue="" disabled={busy}
              onChange={(e) => e.target.value && act({ action: "switch", engine: e.target.value }, "Planned again with that engine.")}>
              <option value="">Use another engine…</option>
              {engines.map((e) => (
                <option key={e.kind} value={e.kind}>{e.name}{beat.eligible.includes(e.kind) ? " (suitable)" : ""}</option>
              ))}
            </select>
            <Button size="sm" variant="secondary" disabled={busy} onClick={() => act({ action: "replan" }, "Planned again.")}>Plan again</Button>
          </div>
          {beat.engine && beat.data && (
            <div className="space-y-2">
              <p className="text-xs font-medium">Texts (must stay true to the cited fact)</p>
              {statements(draft).map((st) => (
                <label key={st.path.join(".")} className="block text-xs">
                  <span className="text-muted-foreground">{st.path.join(" › ")} {st.basis ? `· ${st.basis}` : ""}</span>
                  <input className="mt-0.5 w-full rounded border px-2 py-1" value={st.text}
                    onChange={(e) => setDraft(withText(draft, st.path, e.target.value))} />
                </label>
              ))}
              <Button size="sm" disabled={busy} onClick={() => act({ action: "edit", data: draft }, "Texts saved.")}>Save texts</Button>
            </div>
          )}
          {note && <p role="status" className="text-xs">{note}</p>}
        </div>
      )}
    </div>
  );
}

/** What the planner chose for each beat among the eight cinematic engines, why, and the owner's controls. */
export function EnginesPanel({ caseId, refreshKey }: { caseId: number; refreshKey: number }) {
  const { data, error } = useApi(() => mvpApi.engines(caseId), [caseId, refreshKey], { keepPrevious: true });
  const [updated, setUpdated] = useState<MvpEngines | null>(null);
  const view = updated ?? data;
  if (error && !view) return null;
  if (!view || view.beats.length === 0) return null;
  const chosen = view.beats.filter((b) => b.engine);
  return (
    <Card data-testid="engines-panel">
      <CardHeader>
        <CardTitle className="text-sm">Cinematic engines · {chosen.length} chosen, {view.on_screen} in the {view.language.toUpperCase()} film</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {(chosen.length ? chosen : []).map((b) => (
          <BeatRow key={b.beat} caseId={caseId} beat={b} engines={view.engines} onChanged={setUpdated} />
        ))}
        {chosen.length === 0 && <p className="text-sm text-muted-foreground">The planner found no beat that one of the engines would make clearer.</p>}
        <p className="text-xs text-muted-foreground">Changes apply the next time the film is directed (Resume).</p>
      </CardContent>
    </Card>
  );
}
