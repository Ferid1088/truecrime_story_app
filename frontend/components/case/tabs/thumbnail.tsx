"use client";

import { useState } from "react";
import { Check, ImageIcon, X } from "lucide-react";
import { API_BASE } from "@/lib/config";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { NamingLanguage, ThumbnailRecord } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { TableSkeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/state";

const LANGS: { code: NamingLanguage; name: string }[] = [
  { code: "en", name: "English · ClueVera" },
  { code: "de", name: "German · Fallspur" },
  { code: "fa", name: "Persian · رد خاموش" },
  { code: "ar", name: "Arabic · أثر خفي" },
];

const FIELDS: [keyof ThumbnailRecord["critic"]["scorecard"], string, boolean][] = [
  ["host_visibility", "Host visibility", false],
  ["case_visual_clarity", "Case picture clarity", false],
  ["brand_consistency", "Brand consistency", false],
  ["cleanliness", "Cleanliness", false],
  ["curiosity", "Curiosity", false],
  ["authenticity", "Authenticity", false],
  ["readability", "Readability", false],
  ["crowding", "Crowding", true],
  ["spoiler_risk", "Spoiler risk", true],
  ["misleading_risk", "Misleading risk", true],
  ["automation_feel", "Automation feel", true],
];

function tone(v: number | null, risk: boolean) {
  if (v == null) return "default" as const;
  const good = risk ? 1 - v : v;
  return good >= 0.75 ? ("success" as const) : good >= 0.55 ? ("warning" as const) : ("danger" as const);
}

/**
 * The episode thumbnail per channel: the video's host (same outfit), one real
 * case picture, the localized status badge. Compose, read the critic's
 * scorecard, then approve or reject — a person always decides.
 */
export function ThumbnailTab({ caseId }: { caseId: number }) {
  const [lang, setLang] = useState<NamingLanguage>("en");
  const [tick, setTick] = useState(0);
  const [side, setSide] = useState<"auto" | "left" | "right">("auto");
  const [pose, setPose] = useState("");
  const [primary, setPrimary] = useState<number | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const list = useApi(() => api.thumbnails(caseId, lang), [caseId, lang, tick], { keepPrevious: true });
  const options = useApi(() => api.thumbnailOptions(caseId, lang), [caseId, lang, tick], {
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

  const current = list.data?.find((t) => t.status === "approved") ?? list.data?.[0] ?? null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Thumbnail</CardTitle>
          <Select
            value={lang}
            onChange={(e) => setLang(e.target.value as NamingLanguage)}
            aria-label="Channel language"
            className="w-56"
          >
            {LANGS.map((l) => (
              <option key={l.code} value={l.code}>
                {l.name}
              </option>
            ))}
          </Select>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          {!options.data && options.loading && <TableSkeleton rows={3} cols={3} />}
          {!options.data && options.error && <ErrorState message={options.error} onRetry={options.refetch} />}
          {options.data && (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant={options.data.outfit_id ? "accent" : "warning"}>
                  host outfit: {options.data.outfit_id ?? "unknown"}
                </Badge>
                <Badge variant="outline">{options.data.host_poses.length} approved poses</Badge>
                <Badge variant="outline">{options.data.usable.length} usable real pictures</Badge>
              </div>
              {options.data.host_error && (
                <p className="text-amber-600 dark:text-amber-400">{options.data.host_error}</p>
              )}

              <div>
                <p className="mb-1 text-xs font-medium uppercase text-muted-foreground">Primary case picture</p>
                {options.data.usable.length === 0 ? (
                  <p className="text-muted-foreground">
                    No usable real picture yet: it must be verified for this case, rights-cleared and free of
                    spoilers.
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {options.data.usable.map((a, i) => (
                      <button
                        key={a.asset_id}
                        type="button"
                        onClick={() => setPrimary(a.asset_id)}
                        aria-pressed={(primary ?? options.data!.usable[0].asset_id) === a.asset_id}
                        className={`w-28 cursor-pointer rounded-md border p-1 text-left text-[11px] ${
                          (primary ?? options.data!.usable[0].asset_id) === a.asset_id
                            ? "border-primary"
                            : "border-border"
                        }`}
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={`${API_BASE}${a.thumb}`} alt={a.title ?? a.code} className="h-16 w-full rounded object-cover" />
                        <span className="mt-1 block truncate">{a.kind}{i === 0 ? " · best" : ""}</span>
                        <span className="block truncate text-muted-foreground">{a.rights}</span>
                      </button>
                    ))}
                  </div>
                )}
                {options.data.rejected.length > 0 && (
                  <details className="mt-2 text-xs text-muted-foreground">
                    <summary className="cursor-pointer">{options.data.rejected.length} pictures not usable</summary>
                    <ul className="mt-1 space-y-0.5">
                      {options.data.rejected.map((a) => (
                        <li key={a.asset_id}>
                          {a.title ?? a.code} — {a.reason}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>

              <div className="flex flex-wrap items-end gap-3">
                <label className="text-xs">
                  Host side
                  <Select value={side} onChange={(e) => setSide(e.target.value as typeof side)} className="mt-1 w-32">
                    <option value="auto">auto</option>
                    <option value="left">left</option>
                    <option value="right">right</option>
                  </Select>
                </label>
                <label className="text-xs">
                  Host pose
                  <Select value={pose} onChange={(e) => setPose(e.target.value)} className="mt-1 w-44">
                    <option value="">auto</option>
                    {options.data.host_poses.map((p) => (
                      <option key={p.id} value={p.pose}>
                        {p.pose} ({p.facing})
                      </option>
                    ))}
                  </Select>
                </label>
                <label className="text-xs">
                  Text (0–4 words)
                  <Input value={text} onChange={(e) => setText(e.target.value)} className="mt-1 w-48" maxLength={60} />
                </label>
                <Button
                  size="sm"
                  loading={busy === "compose"}
                  onClick={() =>
                    run("compose", () =>
                      api.composeThumbnail(caseId, {
                        language: lang,
                        side: side === "auto" ? undefined : side,
                        pose: pose || undefined,
                        primary_asset_id: primary ?? undefined,
                        text,
                      }),
                    )
                  }
                >
                  <ImageIcon className="size-3.5" /> Compose thumbnail
                </Button>
              </div>
              {message && <p className="text-rose-600 dark:text-rose-400">{message}</p>}
            </>
          )}
        </CardContent>
      </Card>

      {current && (
        <Card>
          <CardHeader>
            <CardTitle>
              Version {current.version} <Badge variant={current.status === "approved" ? "success" : "outline"}>{current.status}</Badge>
            </CardTitle>
            <div className="flex gap-2">
              <Button size="sm" loading={busy === "approve"} disabled={current.status === "approved"}
                onClick={() => run("approve", () => api.decideThumbnail(current.id, true, current.critic.verdict === "fail"))}>
                <Check className="size-3.5" /> {current.critic.verdict === "fail" ? "Approve anyway" : "Approve"}
              </Button>
              <Button size="sm" variant="outline" loading={busy === "reject"} disabled={current.status === "rejected"}
                onClick={() => run("reject", () => api.decideThumbnail(current.id, false))}>
                <X className="size-3.5" /> Reject
              </Button>
            </div>
          </CardHeader>
          <CardContent className="grid gap-4 lg:grid-cols-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={`${API_BASE}${current.image_url}`}
              alt={`Thumbnail ${current.brief.channel}`}
              className="w-full rounded-md border border-border"
              data-testid="thumbnail-preview"
            />
            <div className="space-y-3 text-sm">
              <div className="flex flex-wrap gap-2">
                <Badge variant="accent">badge: {current.status_label}</Badge>
                <Badge variant="outline">{current.brief.channel}</Badge>
                <Badge variant="outline">host {current.brief.host.side} · {current.brief.host.pose}</Badge>
                <Badge variant={current.critic.verdict === "fail" ? "danger" : current.critic.verdict === "pass" ? "success" : "warning"}>
                  critic: {current.critic.verdict.replace("_", " ")}
                </Badge>
              </div>
              <table className="w-full text-sm" data-testid="scorecard">
                <tbody>
                  {FIELDS.map(([k, label, risk]) => {
                    const v = current.critic.scorecard[k];
                    return (
                      <tr key={k} className="border-t border-border">
                        <td className="py-1">{label}{risk ? " (risk)" : ""}</td>
                        <td className="text-right">
                          <Badge variant={tone(v, risk)}>{v == null ? "—" : v.toFixed(2)}</Badge>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {current.critic.failures.length > 0 && (
                <ul className="list-disc pl-5 text-rose-600 dark:text-rose-400">
                  {current.critic.failures.map((f) => (
                    <li key={f}>{f}</li>
                  ))}
                </ul>
              )}
              {!current.critic.vision_checked && (
                <p className="text-xs text-muted-foreground">
                  The vision critic was not available; curiosity, authenticity and automation feel need your judgment.
                </p>
              )}
              {current.critic.reason && <p className="text-muted-foreground">{current.critic.reason}</p>}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
