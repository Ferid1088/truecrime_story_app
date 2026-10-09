"use client";

import { useState } from "react";
import { Check, Pencil, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { CaseNaming, NamingLanguage, TitleCandidate } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { TableSkeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/state";

const LANGS: { code: NamingLanguage; name: string; rtl: boolean }[] = [
  { code: "en", name: "English", rtl: false },
  { code: "de", name: "German", rtl: false },
  { code: "fa", name: "Persian", rtl: true },
  { code: "ar", name: "Arabic", rtl: true },
];

const score = (v: number | null) => (v == null ? "—" : v.toFixed(2));

function riskVariant(v: number | null) {
  if (v == null) return "default" as const;
  return v > 0.4 ? ("danger" as const) : v > 0.2 ? ("warning" as const) : ("success" as const);
}

/**
 * Case Naming and Episode Identity: exactly 7 ranked, collision-checked
 * candidates per channel language, the approved title, the localized status
 * and the final YouTube title. The internal sequence stays secondary.
 */
export function NamingTab({ caseId }: { caseId: number }) {
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const { data, error, loading, refetch } = useApi(() => api.caseNaming(caseId), [caseId, tick], {
    keepPrevious: true,
  });

  async function run(key: string, fn: () => Promise<unknown>) {
    setBusy(key);
    setMessage(null);
    try {
      await fn();
      setTick((t) => t + 1);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (!data && loading) return <TableSkeleton rows={4} cols={5} />;
  if (!data && error) return <ErrorState message={error} onRetry={refetch} />;
  if (!data) return null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Case naming</CardTitle>
          <Button
            size="sm"
            onClick={() => run("all", () => api.generateNaming(caseId))}
            loading={busy === "all"}
          >
            <Sparkles className="size-3.5" /> Generate 7 titles per language
          </Button>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={data.provenance.public_status ? "accent" : "warning"}>
              status: {data.provenance.public_status ?? "needs review"}
            </Badge>
            {data.editorial_concept && <span className="text-muted-foreground">Concept: {data.editorial_concept}</span>}
          </div>
          {data.provenance.needs_review && (
            <p className="text-amber-600 dark:text-amber-400">
              The resolution status is not SOLVED or UNSOLVED yet, so titles can be drafted but no public YouTube title is built.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Collision checks use the app&apos;s own cases, titles and already-collected research titles. They are
            not verified against the open internet. Corpus:{" "}
            {Object.entries(data.corpus)
              .map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`)
              .join(" · ") || "empty"}
            .
          </p>
          {message && <p className="text-rose-600 dark:text-rose-400">{message}</p>}
        </CardContent>
      </Card>

      {LANGS.map((l) => (
        <LanguageCard
          key={l.code}
          caseId={caseId}
          lang={l}
          state={data.languages[l.code]}
          busy={busy}
          run={run}
        />
      ))}
    </div>
  );
}

function LanguageCard({
  caseId,
  lang,
  state,
  busy,
  run,
}: {
  caseId: number;
  lang: (typeof LANGS)[number];
  state: CaseNaming["languages"][NamingLanguage];
  busy: string | null;
  run: (key: string, fn: () => Promise<unknown>) => Promise<void>;
}) {
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [draft, setDraft] = useState("");
  const dir = lang.rtl ? "rtl" : "ltr";
  const ident = state.identity;

  return (
    <Card>
      <CardHeader>
        <CardTitle>{lang.name}</CardTitle>
        <div className="flex items-center gap-2">
          {state.short_by > 0 && <Badge variant="warning">{state.short_by} missing of {state.target}</Badge>}
          <Button
            size="sm"
            variant="secondary"
            loading={busy === `gen-${lang.code}`}
            onClick={() => run(`gen-${lang.code}`, () => api.generateNaming(caseId, [lang.code]))}
          >
            {state.candidates.length ? "Refill to 7" : "Generate"}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {ident && (
          <div className="rounded-md border border-border bg-muted/30 p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium" dir={dir}>{ident.editorial_title ?? "—"}</span>
              {ident.resolution_label && <Badge variant="accent">{ident.resolution_label}</Badge>}
              <Badge variant={ident.published ? "success" : "outline"}>
                {ident.published ? `published · v${ident.title_version}` : "not published"}
              </Badge>
            </div>
            <p className="mt-1 break-words" dir={dir} data-testid={`youtube-title-${lang.code}`}>
              {ident.youtube_title ?? "No public title yet (status unknown or no title approved)"}
            </p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              {ident.case_uid} · channel {ident.channel_id}
              {ident.episode_sequence != null && <> · internal sequence {ident.episode_sequence}</>}
            </p>
          </div>
        )}

        {state.candidates.length === 0 ? (
          <p className="text-sm text-muted-foreground">No candidates yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-[11px] uppercase text-muted-foreground">
                <tr>
                  <th className="py-1 pr-3">#</th>
                  <th className="pr-3">Title</th>
                  <th className="pr-3">Collision</th>
                  <th className="pr-3">Mem</th>
                  <th className="pr-3">Cur</th>
                  <th className="pr-3">Spec</th>
                  <th className="pr-3">Spoiler</th>
                  <th className="pr-3">Epistemic</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {state.candidates.map((c) => (
                  <CandidateRow
                    key={c.id}
                    c={c}
                    dir={dir}
                    editing={editing === c.id}
                    draft={draft}
                    setDraft={setDraft}
                    onEdit={() => {
                      setEditing(c.id);
                      setDraft(c.title);
                    }}
                    onCancel={() => setEditing(null)}
                    onSave={() =>
                      run(`edit-${c.id}`, async () => {
                        await api.manualTitle(caseId, lang.code, draft);
                        setEditing(null);
                      })
                    }
                    onApprove={() => run(`ok-${c.id}`, () => api.approveTitle(caseId, c.id, !!ident?.published))}
                    busy={busy}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2">
          {editing === "new" ? (
            <>
              <Input
                dir={dir}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Custom title"
                className="max-w-xs"
              />
              <Button
                size="sm"
                loading={busy === "edit-new"}
                onClick={() =>
                  run("edit-new", async () => {
                    await api.manualTitle(caseId, lang.code, draft);
                    setEditing(null);
                  })
                }
              >
                Check &amp; add
              </Button>
              <Button size="sm" variant="outline" onClick={() => setEditing(null)}>
                Cancel
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                setEditing("new");
                setDraft("");
              }}
            >
              <Pencil className="size-3.5" /> Custom title
            </Button>
          )}
        </div>

        {state.rejected.length > 0 && (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer">{state.rejected.length} rejected (kept so they are not proposed again)</summary>
            <ul className="mt-2 space-y-1">
              {state.rejected.map((c) => (
                <li key={c.id}>
                  <span dir={dir} className="font-medium text-foreground/80">{c.title}</span> — {c.rejection_reason}
                </li>
              ))}
            </ul>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

function CandidateRow({
  c,
  dir,
  editing,
  draft,
  setDraft,
  onEdit,
  onCancel,
  onSave,
  onApprove,
  busy,
}: {
  c: TitleCandidate;
  dir: "ltr" | "rtl";
  editing: boolean;
  draft: string;
  setDraft: (v: string) => void;
  onEdit: () => void;
  onCancel: () => void;
  onSave: () => void;
  onApprove: () => void;
  busy: string | null;
}) {
  return (
    <tr className="border-t border-border align-top">
      <td className="py-2 pr-3 text-xs text-muted-foreground">{c.rank ?? ""}</td>
      <td className="pr-3">
        {editing ? (
          <Input dir={dir} value={draft} onChange={(e) => setDraft(e.target.value)} />
        ) : (
          <div>
            <span dir={dir} className="font-medium">{c.title}</span>
            <div className="mt-0.5 flex gap-1">
              {c.recommended && <Badge variant="success">recommended</Badge>}
              {c.status === "selected" && <Badge variant="accent">approved</Badge>}
              {c.origin === "manual" && <Badge variant="outline">manual</Badge>}
            </div>
          </div>
        )}
      </td>
      <td className="pr-3">
        <Badge variant={c.collision_status === "clear" ? "success" : "danger"}>{c.collision_status}</Badge>
        <div className="text-[11px] text-muted-foreground">
          near {c.near_collision_score == null ? "—" : c.near_collision_score.toFixed(0)}
          {c.semantic_collision_score != null && <> · sem {c.semantic_collision_score.toFixed(2)}</>}
        </div>
      </td>
      <td className="pr-3 tabular-nums">{score(c.memorability)}</td>
      <td className="pr-3 tabular-nums">{score(c.curiosity)}</td>
      <td className="pr-3 tabular-nums">{score(c.specificity)}</td>
      <td className="pr-3"><Badge variant={riskVariant(c.spoiler_risk)}>{score(c.spoiler_risk)}</Badge></td>
      <td className="pr-3"><Badge variant={riskVariant(c.epistemic_risk)}>{score(c.epistemic_risk)}</Badge></td>
      <td className="whitespace-nowrap text-right">
        {editing ? (
          <>
            <Button size="sm" loading={busy === `edit-${c.id}`} onClick={onSave}>Check</Button>
            <Button size="sm" variant="outline" onClick={onCancel}>Cancel</Button>
          </>
        ) : (
          <>
            <Button size="sm" variant="outline" onClick={onEdit} aria-label={`Edit ${c.title}`}>
              <Pencil className="size-3.5" />
            </Button>{" "}
            <Button
              size="sm"
              variant={c.status === "selected" ? "secondary" : "default"}
              loading={busy === `ok-${c.id}`}
              disabled={c.status === "selected"}
              onClick={onApprove}
            >
              <Check className="size-3.5" /> Approve
            </Button>
          </>
        )}
      </td>
    </tr>
  );
}
