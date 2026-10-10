"use client";

import { RotateCcw, Save, Plus, Minus } from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import Link from "next/link";

type PlatformSettings = {
  enabled: boolean;
  count: number;
  min_seconds: number;
  target_seconds: number;
  max_seconds: number;
};

type ShortFormSettings = {
  candidate_multiplier: number;
  publish_target: number;
  hard_max_seconds: number;
  distribution: Record<string, PlatformSettings>;
  candidate_count: number;
};

const LABELS: Record<string, string> = {
  youtube_short: "YouTube Short",
  instagram_reel: "Instagram Reel",
  facebook_reel: "Facebook Reel",
  tiktok_video: "TikTok Video",
};

export default function ShortFormPage() {
  const { data, error, loading, refetch } = useApi(() => api.shortFormSettings() as Promise<ShortFormSettings>);

  return (
    <div>
      <PageHeader title="Short-Form" description="Distribution settings for vertical clips." actions={<Link href="/short-form/review" className="rounded-md border px-3 py-2 text-sm hover:bg-muted">Review pilot</Link>} />

      {loading && <Skeleton className="h-72" />}
      {error && <ErrorState message={error} onRetry={refetch} />}
      {data && <ShortFormEditor key={JSON.stringify(data)} initial={data} />}
    </div>
  );
}

function ShortFormEditor({ initial }: { initial: ShortFormSettings }) {
  const [settings, setSettings] = useState<ShortFormSettings>(initial);
  const [saved, setSaved] = useState<ShortFormSettings>(initial);
  const [languageMode, setLanguageMode] = useState("episode");
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const errors = useMemo(() => validate(settings), [settings]);
  const dirty = JSON.stringify(settings) !== JSON.stringify(saved);
  const summary = useMemo(() => summarize(settings), [settings]);

  const updatePlatform = (platform: string, patch: Partial<PlatformSettings>) => {
    setSettings((current) =>
      current
        ? {
            ...current,
            distribution: {
              ...current.distribution,
              [platform]: { ...current.distribution[platform], ...patch },
            },
          }
        : current,
    );
  };

  const resetRow = (platform: string) => {
    updatePlatform(platform, initial.distribution[platform]);
  };

  const resetAll = () => {
    setSettings(initial);
    setLanguageMode("episode");
    setMessage("");
  };

  const save = async () => {
    if (errors.length) return;
    setSaving(true);
    setMessage("");
    try {
      const next = (await api.updateShortFormSettings({
        distribution: settings.distribution,
        language_mode: languageMode,
      })) as ShortFormSettings;
      setSettings(next);
      setSaved(next);
      setMessage("Saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-border px-3 py-2">
            <label className="text-sm text-muted-foreground" htmlFor="shortform-language">
              Language
            </label>
            <select
              id="shortform-language"
              className="h-8 rounded-md border border-border bg-background px-2 text-sm"
              value={languageMode}
              onChange={(e) => setLanguageMode(e.target.value)}
            >
              <option value="episode">Episode default</option>
              <option value="all">All four languages</option>
            </select>
            <span className="ml-auto text-sm text-muted-foreground">
              {summary.totalVideos} target videos · ~{summary.runtime}s runtime · {summary.candidates} candidates
            </span>
            {dirty && <span className="text-xs font-medium text-amber-600">Unsaved changes</span>}
          </div>

          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="bg-muted/50 text-left text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2">Platform</th>
                  <th className="px-3 py-2">Count</th>
                  <th className="px-3 py-2">Length</th>
                  <th className="px-3 py-2">Level</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(settings.distribution).map(([platform, row]) => (
                  <tr key={platform} className="border-t border-border">
                    <td className="px-3 py-2">
                      <Checkbox
                        checked={row.enabled}
                        onChange={(e) => updatePlatform(platform, { enabled: e.currentTarget.checked })}
                        label={LABELS[platform] ?? platform}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex w-36 items-center gap-1">
                        <Button
                          type="button"
                          size="icon"
                          variant="secondary"
                          aria-label={`Decrease ${LABELS[platform]} count`}
                          onClick={() => updatePlatform(platform, { count: Math.max(0, row.count - 1) })}
                        >
                          <Minus />
                        </Button>
                        <Input
                          aria-label={`${LABELS[platform]} count`}
                          type="number"
                          min={0}
                          max={20}
                          value={row.count}
                          onChange={(e) => updatePlatform(platform, { count: Number(e.target.value) })}
                        />
                        <Button
                          type="button"
                          size="icon"
                          variant="secondary"
                          aria-label={`Increase ${LABELS[platform]} count`}
                          onClick={() => updatePlatform(platform, { count: Math.min(20, row.count + 1) })}
                        >
                          <Plus />
                        </Button>
                      </div>
                    </td>
                    <td className="px-3 py-2">
                      <div className="grid grid-cols-3 gap-2">
                        <LabeledNumber label="Min" value={row.min_seconds} onChange={(v) => updatePlatform(platform, { min_seconds: v })} />
                        <LabeledNumber label="Target" value={row.target_seconds} onChange={(v) => updatePlatform(platform, { target_seconds: v })} />
                        <LabeledNumber label="Max" value={row.max_seconds} onChange={(v) => updatePlatform(platform, { max_seconds: v })} />
                      </div>
                    </td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">global default</td>
                    <td className="px-3 py-2 text-right">
                      <Button type="button" variant="ghost" size="icon" aria-label={`Reset ${LABELS[platform]}`} onClick={() => resetRow(platform)}>
                        <RotateCcw />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="secondary" onClick={resetAll}>
              <RotateCcw /> Reset all
            </Button>
            <Button type="button" onClick={save} disabled={!dirty || errors.length > 0} loading={saving}>
              <Save /> Save
            </Button>
            <span className="text-sm text-muted-foreground">Capacity hint: this episode has ~8 distinct usable beats.</span>
            {summary.totalVideos > 8 && (
              <span className="text-sm font-medium text-amber-600">Requested {summary.totalVideos}, capacity ~8</span>
            )}
            {message && <span className="text-sm text-muted-foreground">{message}</span>}
          </div>

          {errors.length > 0 && (
            <div className="rounded-md border border-destructive/40 px-3 py-2 text-sm text-destructive">
              {errors.join(" · ")}
            </div>
          )}
      </div>
    </div>
  );
}

function LabeledNumber({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  return (
    <label className="space-y-1">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <Input type="number" min={0} max={60} value={value} onChange={(e) => onChange(Number(e.target.value))} />
    </label>
  );
}

function validate(settings: ShortFormSettings | null): string[] {
  if (!settings) return [];
  const errors: string[] = [];
  for (const [platform, row] of Object.entries(settings.distribution)) {
    const label = LABELS[platform] ?? platform;
    if (row.count < 0 || row.count > 20) errors.push(`${label}: count must be 0–20`);
    if (!(row.min_seconds <= row.target_seconds && row.target_seconds <= row.max_seconds))
      errors.push(`${label}: min <= target <= max`);
    if (row.max_seconds > settings.hard_max_seconds) errors.push(`${label}: max must be <= ${settings.hard_max_seconds}s`);
  }
  return errors;
}

function summarize(settings: ShortFormSettings | null) {
  if (!settings) return { totalVideos: 0, runtime: 0, candidates: 0 };
  const enabled = Object.values(settings.distribution).filter((p) => p.enabled);
  const totalVideos = enabled.reduce((sum, p) => sum + p.count, 0);
  const runtime = enabled.reduce((sum, p) => sum + p.count * p.target_seconds, 0);
  const maxCount = Math.max(...enabled.map((p) => p.count));
  return {
    totalVideos,
    runtime,
    candidates: Math.round(maxCount * settings.candidate_multiplier),
  };
}
