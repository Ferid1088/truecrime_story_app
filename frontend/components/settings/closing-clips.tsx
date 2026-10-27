"use client";

import { useState } from "react";
import { Clapperboard } from "lucide-react";
import { closingApi, ApiError, api, type ClosingCreateResult } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";

const SWITCHES = [
  {
    key: "use_stored" as const,
    label: "Use stored closing clips",
    on: "A stored variant is picked at random, never the same twice in a row.",
    off: "A fresh closing clip is rendered for every video.",
  },
  {
    key: "create_new" as const,
    label: "Create new closing clips",
    on: "A language below its target gets a new variant rendered while a video is made.",
    off: "No closing clip is ever rendered; only what is stored is used.",
  },
];

const eur = (n: number) => `€${n.toFixed(2)}`;

export function ClosingClipsCard() {
  const { data, refetch } = useApi(() => closingApi.overview(), [], { keepPrevious: true });
  const cases = useApi(() => api.listCases(), [], { keepPrevious: true });
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [caseId, setCaseId] = useState<number | null>(null);
  const [pending, setPending] = useState<{ language: string; plan: ClosingCreateResult["estimate"] } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!data) return null;
  const payer = caseId ?? cases.data?.[0]?.id ?? null;

  async function toggle(key: "use_stored" | "create_new", value: boolean) {
    setMessage(null);
    try {
      await closingApi.setSettings({ [key]: value });
      setMessage({ ok: true, text: "Saved. It applies to the next video." });
      refetch();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : "Could not save." });
    }
  }

  async function estimate(language: string) {
    if (payer == null) return;
    setMessage(null);
    try {
      const r = await closingApi.createMissing(language, payer, false);
      setPending({ language, plan: r.estimate });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : "Could not estimate." });
    }
  }

  async function render(language: string) {
    if (payer == null) return;
    setBusy(true);
    try {
      const r = await closingApi.createMissing(language, payer, true);
      setMessage({
        ok: true,
        text: `${r.created.length} variant(s) created for ${langLabel(language)}; actual cost ${eur(r.actual_eur ?? 0)}.`,
      });
      setPending(null);
      refetch();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof ApiError ? e.message : "Rendering failed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mt-6" data-testid="closing-clips-card">
      <CardContent className="space-y-4 py-4">
        <div className="flex items-center gap-2">
          <Clapperboard className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">Host closing clips</h2>
          <span className="text-xs text-muted-foreground">
            Closing of every short: {data.closing_order === "cta_then_identity" ? "call to action, then identity line" : "identity line, then call to action"}
          </span>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          {SWITCHES.map((s) => {
            const value = data.settings[s.key];
            return (
              <div key={s.key} className="rounded-md border border-border p-3 text-sm" data-testid={`closing-${s.key}`}>
                <div className="flex items-center justify-between gap-2">
                  <Checkbox
                    label={s.label}
                    checked={value}
                    onChange={(e) => toggle(s.key, e.target.checked)}
                  />
                  <Badge variant={value ? "success" : "warning"}>{value ? "ON" : "OFF"}</Badge>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">{value ? s.on : s.off}</p>
              </div>
            );
          })}
        </div>
        {message && (
          <p className={`text-xs ${message.ok ? "text-muted-foreground" : "text-destructive"}`} role="status">
            {message.text}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <label htmlFor="closing-case" className="text-muted-foreground">
            Case that pays for new clips
          </label>
          <select
            id="closing-case"
            className="h-7 rounded-md border border-border bg-transparent px-2"
            value={payer ?? ""}
            onChange={(e) => setCaseId(Number(e.target.value))}
          >
            {(cases.data ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                #{c.id} {c.title}
              </option>
            ))}
          </select>
        </div>

        <table className="w-full text-xs">
          <thead className="text-left text-muted-foreground">
            <tr>
              <th className="py-1 pr-2">Language</th>
              <th className="pr-2">Variants</th>
              <th className="pr-2">Approved looks</th>
              <th className="pr-2">Outfits / positions</th>
              <th className="pr-2">Times used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {Object.entries(data.languages).map(([lang, row]) => (
              <tr key={lang} className="border-t border-border align-top" data-testid={`closing-row-${lang}`}>
                <td className="py-1 pr-2">
                  {langLabel(lang)}
                  <div
                    dir={lang === "fa" || lang === "ar" ? "rtl" : "ltr"}
                    lang={lang}
                    className="mt-1 max-w-xs text-muted-foreground"
                    data-testid={`closing-text-${lang}`}
                  >
                    {row.not_configured?.length ? (
                      <span role="status" className="text-amber-600">
                        Not set up yet: needs {row.not_configured.join(", ")}.
                      </span>
                    ) : row.sentences.join(" ")}
                  </div>
                </td>
                <td className="pr-2">
                  <Badge variant={row.variants >= row.target ? "success" : "warning"}>
                    {row.variants}/{row.target}
                  </Badge>
                </td>
                <td className="pr-2">{row.approved_looks}</td>
                <td className="pr-2">
                  {row.clips.length === 0 ? (
                    <span className="text-muted-foreground">none stored</span>
                  ) : (
                    <ul>
                      {row.clips.map((c) => (
                        <li key={c.id}>
                          {c.outfit} · {c.position} · {c.seconds.toFixed(1)} s
                        </li>
                      ))}
                    </ul>
                  )}
                </td>
                <td className="pr-2">
                  {row.clips.length === 0 ? "–" : row.clips.map((c) => <div key={c.id}>{c.times_used}×</div>)}
                </td>
                <td>
                  {row.missing > 0 && (
                    <Button size="sm" variant="outline" disabled={busy || payer == null} onClick={() => estimate(lang)}>
                      Create missing variants now
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        {pending && (
          <div className="rounded-md border border-border p-3 text-xs" data-testid="closing-estimate">
            <p>
              {langLabel(pending.language)}: {pending.plan.variants} variant(s), about{" "}
              {pending.plan.per_variant.seconds.toFixed(0)} s of avatar each ={" "}
              <strong>{eur(pending.plan.total_eur)}</strong> estimated (voice {eur(pending.plan.per_variant.voice_eur)} +
              avatar {eur(pending.plan.per_variant.avatar_eur)} per variant). Nothing is paid until you confirm.
            </p>
            <div className="mt-2 flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => render(pending.language)}>
                Render now
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setPending(null)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
