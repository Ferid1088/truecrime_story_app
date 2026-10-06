"use client";

import { useState } from "react";
import { GitCompare, Languages, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { LocalizationCompare, StoryMeta } from "@/lib/types";
import { langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const TARGET_LANGUAGES = ["de", "fa", "ar"];

export function Localizations({
  caseId,
  masterReady,
  onOpen,
}: {
  caseId: number;
  masterReady: boolean;
  onOpen: (v: StoryMeta) => void;
}) {
  const { data: locs, refetch } = useApi(
    () => api.listLocalizations(caseId),
    [caseId],
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [compare, setCompare] = useState<LocalizationCompare | null>(null);
  const [error, setError] = useState<string | null>(null);

  const latestByLang = new Map<string, StoryMeta>();
  (locs ?? []).forEach((l) => {
    if (!latestByLang.has(l.language)) latestByLang.set(l.language, l);
  });

  async function generate(lang: string) {
    setBusy(lang);
    setError(null);
    try {
      const v = await api.generateLocalization(caseId, lang);
      onOpen(v);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function improve(v: StoryMeta) {
    setBusy(v.language);
    setError(null);
    try {
      const nv = await api.improveLocalization(v.id);
      onOpen(nv);
      refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function showCompare(v: StoryMeta) {
    setBusy(`cmp-${v.language}`);
    setError(null);
    try {
      setCompare(await api.compareMaster(v.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Languages className="size-4" /> Localizations
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 p-3">
        {!masterReady && (
          <p className="text-[11px] leading-4 text-muted-foreground">
            Localization is enabled once the English Master reaches
            &quot;ready&quot; status.
          </p>
        )}
        {TARGET_LANGUAGES.map((lang) => {
          const v = latestByLang.get(lang);
          return (
            <div
              key={lang}
              className="rounded-md border border-border px-3 py-2"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium">{langLabel(lang)}</span>
                {v ? (
                  <StatusBadge status={v.status} />
                ) : (
                  <span className="text-[11px] text-muted-foreground">
                    not generated
                  </span>
                )}
              </div>
              {v && (
                <div className="mt-1 grid grid-cols-3 gap-1 text-[11px] text-muted-foreground">
                  <span>
                    Native{" "}
                    <b className="text-foreground">
                      {v.native_quality_score != null
                        ? Math.round(v.native_quality_score)
                        : "—"}
                    </b>
                  </span>
                  <span>
                    Sem{" "}
                    <b className="text-foreground">
                      {v.semantic_consistency_score != null
                        ? Math.round(v.semantic_consistency_score)
                        : "—"}
                    </b>
                  </span>
                  <span>
                    Eng{" "}
                    <b className="text-foreground">
                      {Math.round(v.engagement_score)}
                    </b>
                  </span>
                </div>
              )}
              <div className="mt-2 flex gap-1.5">
                {v ? (
                  <>
                    <Button
                      size="sm"
                      variant="secondary"
                      className="h-7 flex-1 text-xs"
                      disabled={busy !== null}
                      onClick={() => onOpen(v)}
                    >
                      Open v{v.version}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 text-xs"
                      disabled={!masterReady || busy !== null}
                      loading={busy === v.language}
                      onClick={() => improve(v)}
                      title="Regenerate from the same master — new version"
                    >
                      <RefreshCw className="size-3" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-7 text-xs"
                      disabled={busy !== null}
                      loading={busy === `cmp-${v.language}`}
                      onClick={() => showCompare(v)}
                      title="Compare to English Master"
                    >
                      <GitCompare className="size-3" />
                    </Button>
                  </>
                ) : (
                  <Button
                    size="sm"
                    variant="secondary"
                    className="h-7 w-full text-xs"
                    disabled={!masterReady || busy !== null}
                    loading={busy === lang}
                    onClick={() => generate(lang)}
                  >
                    Generate
                  </Button>
                )}
              </div>
            </div>
          );
        })}
        {error && (
          <p className="rounded-md border border-rose-500/30 bg-rose-500/5 px-2 py-1.5 text-[11px] text-rose-600 dark:text-rose-400">
            {error}
          </p>
        )}
        {compare && (
          <ComparePanel
            compare={compare}
            onClose={() => setCompare(null)}
          />
        )}
      </CardContent>
    </Card>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    ready: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
    needs_revision: "bg-amber-500/15 text-amber-600 dark:text-amber-400",
    outdated: "bg-zinc-500/15 text-zinc-500",
    draft: "bg-sky-500/15 text-sky-600 dark:text-sky-400",
    failed: "bg-rose-500/15 text-rose-600 dark:text-rose-400",
  };
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${map[status] ?? map.draft}`}
    >
      {status === "ready" ? "ready for human review" : status.replace("_", " ")}
    </span>
  );
}

function ComparePanel({
  compare,
  onClose,
}: {
  compare: LocalizationCompare;
  onClose: () => void;
}) {
  const sem = compare.semantic_consistency ?? {};
  const issues: [string, unknown[]][] = [
    ["Missing information", sem.missing_information ?? []],
    ["Added information", sem.added_information ?? []],
    ["Meaning changes", sem.meaning_changes ?? []],
    ["Uncertainty changes", sem.uncertainty_changes ?? []],
    ["Name/date/number errors", sem.name_date_number_errors ?? []],
  ];
  return (
    <div className="mt-2 rounded-md border border-border">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <p className="text-xs font-medium">
          Master v{compare.master_version ?? "?"} vs{" "}
          {langLabel(compare.localization.language)} v
          {compare.localization.version}
          {sem.semantic_consistency_score != null && (
            <span className="ml-2 text-muted-foreground">
              semantic {Math.round(sem.semantic_consistency_score)}
            </span>
          )}
        </p>
        <Button size="sm" variant="ghost" className="h-6 text-xs" onClick={onClose}>
          Close
        </Button>
      </div>
      <div className="max-h-72 overflow-y-auto p-3">
        {issues.some(([, items]) => items.length > 0) ? (
          <ul className="mb-3 space-y-1.5">
            {issues
              .filter(([, items]) => items.length > 0)
              .map(([label, items]) => (
                <li key={label} className="text-[11px]">
                  <Badge variant="outline" className="mr-1.5">
                    {label}: {items.length}
                  </Badge>
                  {items.slice(0, 3).map((it, i) => (
                    <p key={i} className="mt-1 text-muted-foreground">
                      · {typeof it === "string" ? it : JSON.stringify(it)}
                    </p>
                  ))}
                </li>
              ))}
          </ul>
        ) : (
          <p className="mb-3 text-[11px] text-emerald-600 dark:text-emerald-400">
            No semantic divergences reported.
          </p>
        )}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              English Master
            </p>
            <p className="whitespace-pre-wrap text-[11px] leading-5 text-foreground/80">
              {(compare.master_text ?? "").slice(0, 3000)}
            </p>
          </div>
          <div>
            <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              {langLabel(compare.localization.language)} Localization
            </p>
            <p
              className="whitespace-pre-wrap text-[11px] leading-5 text-foreground/80"
              dir="auto"
            >
              {compare.localized_text.slice(0, 3000)}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
