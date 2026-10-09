"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Clapperboard, Mic } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatTimecode, isRtl, langLabel } from "@/lib/format";
import type { HostScene } from "@/lib/types";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { InlineAlert, LanguagePicker, type LanguageTabProps } from "../shared";

/** The saved steps of a host scene, in order. */
const STEPS: { status: string; label: string }[] = [
  { status: "planned", label: "Text" },
  { status: "voice_ready", label: "Voice" },
  { status: "avatar_uploaded", label: "Audio uploaded" },
  { status: "avatar_requested", label: "Avatar requested" },
  { status: "avatar_ready", label: "Avatar video" },
];
const FAILED_AT: Record<string, number> = {
  voice: 1,
  avatar_upload: 2,
  avatar_request: 3,
  avatar_download: 4,
};

/** Host scenes: each written host segment → voice → avatar video. Every
 * step is saved before the next; a failed step is retried from there. */
export function HostTab({ caseId, settings, refreshKey, language, onLanguageChange }: LanguageTabProps) {
  const [tick, setTick] = useState(0);
  const { data, error, loading, refetch } = useApi(
    () => api.hostScenes({ case_id: caseId, language }),
    [caseId, language, refreshKey, tick],
    { keepPrevious: true },
  );
  const running = (data ?? []).some((s) => s.running);
  useEffect(() => {
    if (!running) return;
    const t = setInterval(() => setTick((n) => n + 1), 4000);
    return () => clearInterval(t);
  }, [running]);
  const avatarOn = !!settings.avatar_generation?.enabled;
  const channel = settings.channels?.[language]?.name;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <LanguagePicker languages={settings.languages} value={language} onChange={onLanguageChange} />
        <p className="text-xs text-muted-foreground">
          {channel ? `${channel} studio · ` : ""}
          {avatarOn ? "avatar generation on" : "avatar generation off (config avatar.enabled)"}
        </p>
      </div>
      {loading && !data && <Skeleton className="h-40" />}
      {error && !data && <ErrorState message={error} onRetry={refetch} />}
      {data && data.length === 0 && (
        <EmptyState
          title="No host scenes yet"
          description={`The host stage plans one scene per written host segment (${langLabel(language)}).`}
        />
      )}
      {[...(data ?? [])]
        .sort((a, b) => b.host_segments_id - a.host_segments_id || a.host_segment_id.localeCompare(b.host_segment_id))
        .map((s) => (
        <SceneCard key={s.id} scene={s} avatarOn={avatarOn} onChanged={() => setTick((n) => n + 1)} />
      ))}
    </div>
  );
}

function SceneCard({ scene, avatarOn, onChanged }: { scene: HostScene; avatarOn: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const reached = STEPS.findIndex((st) => st.status === scene.status);
  const failedAt = scene.failed_step ? FAILED_AT[scene.failed_step] : undefined;

  const run = async (until: "voice" | "avatar") => {
    setBusy(true);
    setErr(null);
    try {
      await api.runHostScene(scene.id, until);
      onChanged();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {scene.host_segment_id} · {scene.position ?? "—"}
          {scene.beat_id && <span className="text-muted-foreground"> after {scene.beat_id}</span>}
        </CardTitle>
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="outline">{scene.framing_preset.replace("HOST_", "").toLowerCase()}</Badge>
          {scene.running && <Badge variant="info">running</Badge>}
          {scene.failed_step && <Badge variant="danger">failed: {scene.failed_step.replace("_", " ")}</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[14rem_1fr]">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={apiFileUrl(`/api/studios/assets/${scene.studio_asset_id}/image`)}
            alt={`${scene.channel} studio`}
            className="aspect-video w-full rounded border border-border object-cover"
          />
          <div className="space-y-2">
            <p dir={isRtl(scene.language) ? "rtl" : "ltr"} className="text-sm leading-relaxed">
              {scene.text}
            </p>
            <p className="text-xs text-muted-foreground">
              {scene.channel} · text {scene.text_sha256.slice(0, 10)}
              {scene.planned_duration != null && ` · ~${formatTimecode(scene.planned_duration)}`}
              {scene.voice_seconds != null && ` · voice ${formatTimecode(scene.voice_seconds)}`}
              {scene.provider_job_id && ` · job ${scene.provider_job_id.slice(0, 10)}`}
              {scene.provider_generation > 0 && ` · request #${scene.provider_generation + 1}`}
            </p>
          </div>
        </div>

        <ol className="flex flex-wrap items-center gap-1.5 text-xs">
          {STEPS.map((st, i) => {
            const variant: NonNullable<BadgeProps["variant"]> =
              failedAt === i ? "danger" : i <= reached ? "success" : "default";
            return (
              <li key={st.status} className="flex items-center gap-1.5">
                <Badge variant={variant}>{st.label}</Badge>
                {i < STEPS.length - 1 && <span className="text-muted-foreground">→</span>}
              </li>
            );
          })}
        </ol>

        {scene.last_error && <InlineAlert tone="error">{scene.last_error}</InlineAlert>}
        {err && <InlineAlert tone="error">{err}</InlineAlert>}

        {scene.voice_ready && (
          <audio controls preload="none" src={apiFileUrl(`/api/host-scenes/${scene.id}/voice`)} className="h-8 w-full" />
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" disabled={busy || scene.running} onClick={() => run("voice")}>
            <Mic className="size-3.5" /> {scene.voice_ready ? "Check voice" : scene.failed_step === "voice" ? "Retry voice" : "Make voice"}
          </Button>
          <Button
            size="sm"
            disabled={busy || scene.running || !avatarOn}
            title={avatarOn ? undefined : "Avatar generation is off (config avatar.enabled)"}
            onClick={() => run("avatar")}
          >
            <Clapperboard className="size-3.5" />{" "}
            {scene.avatar_ready ? "Check avatar" : scene.failed_step?.startsWith("avatar") ? "Retry avatar" : "Make avatar"}
          </Button>
          <span className="text-[11px] text-muted-foreground">
            Saved steps are reused — a retry continues where it stopped.
          </span>
        </div>

        <button
          type="button"
          className="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />} History ({scene.history.length})
        </button>
        {open && (
          <ul className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
            {scene.history.map((h, i) => (
              <li key={i}>
                {h.at.slice(0, 19).replace("T", " ")} · {h.step} · {h.outcome}
                {h.detail ? ` · ${typeof h.detail === "string" ? h.detail : JSON.stringify(h.detail)}` : ""}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
