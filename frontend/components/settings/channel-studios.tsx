"use client";

import { useState } from "react";
import { AlertTriangle, CheckCircle2, ShieldCheck, XCircle } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import type {
  StudioAssetView,
  StudioChannel,
  StudioPatch,
  StudioZone,
  StudiosResponse,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/state";

const PRESET_LABELS: Record<string, string> = {
  HOST_CLOSE: "Close",
  HOST_MEDIUM: "Medium",
  HOST_WIDE: "Wide",
};

const ZONE_STYLES: { key: "host" | "head" | "logo" | "lower_third"; label: string; cls: string }[] = [
  { key: "host", label: "Host", cls: "border-emerald-400 bg-emerald-400/10" },
  { key: "head", label: "Head", cls: "border-amber-300 bg-amber-300/15" },
  { key: "logo", label: "Logo", cls: "border-rose-500 bg-rose-500/15" },
  { key: "lower_third", label: "Lower third", cls: "border-sky-400 bg-sky-400/15" },
];

const pct = (v: number) => `${(v * 100).toFixed(2)}%`;

function zoneStyle(z: StudioZone) {
  return { left: pct(z.x), top: pct(z.y), width: pct(z.width), height: pct(z.height) };
}

/** Channel studios: which studio each channel's host appears in, its camera
 * angles, the host framing presets and whether a person confirmed them. */
export function ChannelStudios() {
  const { data, error, loading, refetch } = useApi(() => api.studios(), [], { keepPrevious: true });
  const [override, setOverride] = useState<StudiosResponse | null>(null);
  const view = override ?? data;

  return (
    <section className="mt-8">
      <div className="mb-3 flex items-end justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold">Channel studios</h2>
          <p className="text-sm text-muted-foreground">
            Each channel&apos;s host appears only in its own studio. Safe zones and framing come from
            a visual review — confirm them per channel.
          </p>
        </div>
      </div>
      {loading && !view && <Skeleton className="h-64" />}
      {error && !view && <ErrorState message={error} onRetry={refetch} />}
      {view && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          {view.channels.map((c) => (
            <StudioCard
              key={c.language}
              channel={c}
              presets={view.presets}
              onSaved={(next) => setOverride(next)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function StudioCard({
  channel,
  presets,
  onSaved,
}: {
  channel: StudioChannel;
  presets: string[];
  onSaved: (next: StudiosResponse) => void;
}) {
  const prof = channel.profile;
  const byId = Object.fromEntries(channel.assets.map((a) => [a.id, a]));
  const [shown, setShown] = useState<string | null>(null);
  const [zones, setZones] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const current: StudioAssetView | undefined =
    byId[shown ?? ""] ?? (prof ? byId[prof.primary_background] : channel.assets[0]);
  const hostReady = channel.assets.filter((a) => a.approved_for_host);
  const v = channel.validation;
  const confirmed = !!prof?.review?.confirmed;

  const save = async (payload: StudioPatch) => {
    setSaving(true);
    setSaveError(null);
    try {
      onSaved(await api.updateStudio(channel.language, payload));
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const status = !v
    ? { label: "No studio", variant: "danger" as const, Icon: XCircle }
    : !v.ok
      ? { label: "Error", variant: "danger" as const, Icon: XCircle }
      : confirmed
        ? { label: "Ready", variant: "success" as const, Icon: CheckCircle2 }
        : { label: "Needs review", variant: "warning" as const, Icon: AlertTriangle };

  return (
    <Card>
      <CardHeader>
        <CardTitle>
          <span dir="auto">{channel.channel_name}</span>{" "}
          <span className="text-sm font-normal text-muted-foreground">
            · {langLabel(channel.language)}
          </span>
        </CardTitle>
        <Badge variant={status.variant}>
          <status.Icon className="size-3" /> {status.label}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        {current && (
          <div className="space-y-2">
            <div className="relative overflow-hidden rounded-md border border-border bg-muted">
              {current.file_present ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={apiFileUrl(current.image_url)}
                  alt={`${channel.channel_name} — ${current.shot}`}
                  className="block aspect-video w-full object-cover"
                />
              ) : (
                <div className="flex aspect-video items-center justify-center text-sm text-muted-foreground">
                  Image file missing
                </div>
              )}
              {zones &&
                ZONE_STYLES.map(({ key, label, cls }) => {
                  const z = current.safe_zones[key];
                  return z ? (
                    <div
                      key={key}
                      title={label}
                      className={`pointer-events-none absolute border-2 ${cls}`}
                      style={zoneStyle(z)}
                    />
                  ) : null;
                })}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                {current.shot.replaceAll("_", " ")}
                {current.camera_angle !== current.shot && ` · ${current.camera_angle.replaceAll("_", " ")}`} ·{" "}
                {current.width}×{current.height}
                {current.id === prof?.primary_background && " · primary"}
                {current.approved_for_host ? " · host ✓" : " · not for the host"}
              </span>
              <label className="flex cursor-pointer items-center gap-1.5">
                <input type="checkbox" checked={zones} onChange={(e) => setZones(e.target.checked)} />
                Safe zones
                {zones && (
                  <span className="flex gap-1.5 pl-1">
                    {ZONE_STYLES.map(({ key, label, cls }) => (
                      <span key={key} className="flex items-center gap-0.5">
                        <span className={`inline-block size-2.5 border-2 ${cls}`} />
                        {label}
                      </span>
                    ))}
                  </span>
                )}
              </label>
            </div>
            {current.notes && <p className="text-xs text-muted-foreground">{current.notes}</p>}
          </div>
        )}

        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">Camera angles</p>
          <div className="grid grid-cols-4 gap-1.5 sm:grid-cols-7">
            {channel.assets.map((a) => (
              <button
                key={a.id}
                type="button"
                onClick={() => setShown(a.id)}
                title={`${a.shot} — ${a.approved_for_host ? "approved for the host" : "not for the host"}`}
                className={`relative overflow-hidden rounded border text-left ${
                  current?.id === a.id ? "border-foreground" : "border-border"
                }`}
              >
                {a.file_present ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={apiFileUrl(a.image_url)} alt={a.shot} className="block aspect-video w-full object-cover" />
                ) : (
                  <div className="aspect-video bg-muted" />
                )}
                {a.approved_for_host && (
                  <ShieldCheck className="absolute right-0.5 top-0.5 size-3.5 rounded-sm bg-black/60 text-emerald-400" />
                )}
                <span className="block truncate px-1 py-0.5 text-[10px] leading-3 text-muted-foreground">
                  {a.camera ? `${a.camera} · ` : ""}
                  {a.shot.replaceAll("_", " ")}
                </span>
              </button>
            ))}
          </div>
        </div>

        {prof && (
          <div className="space-y-2">
            <Row label="Primary background">
              <Select
                value={prof.primary_background}
                disabled={saving}
                onChange={(e) => save({ primary_background: e.target.value })}
              >
                {hostReady.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.shot.replaceAll("_", " ")}
                  </option>
                ))}
              </Select>
            </Row>
            {presets.map((name) => {
              const p = prof.presets[name];
              return (
                <Row
                  key={name}
                  label={`${PRESET_LABELS[name] ?? name} host`}
                  hint={
                    p
                      ? `host ${Math.round(p.host_height_ratio * 100)}% of frame${
                          p.crop ? " · cropped" : ""
                        }${channel.upscale[name] ? ` · ${channel.upscale[name]}× background` : ""}`
                      : "not defined"
                  }
                >
                  <Select
                    value={p?.asset_id ?? ""}
                    disabled={saving || !p}
                    onChange={(e) => save({ presets: { [name]: e.target.value } })}
                  >
                    {!p && <option value="">—</option>}
                    {hostReady.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.shot.replaceAll("_", " ")}
                      </option>
                    ))}
                  </Select>
                </Row>
              );
            })}
            <Row label="Voice">
              <span className="truncate font-mono text-xs">{channel.elevenlabs_voice_id ?? "—"}</span>
            </Row>
            <Row label="Composition">
              <span className="text-xs">{prof.background_mode.replaceAll("_", " ")}</span>
            </Row>
          </div>
        )}

        {v && (v.errors.length > 0 || v.warnings.length > 0) && (
          <ul className="space-y-1 text-xs">
            {v.errors.map((e) => (
              <li key={e} className="text-rose-600 dark:text-rose-400">
                {e}
              </li>
            ))}
            {v.warnings.map((w) => (
              <li key={w} className="text-amber-700 dark:text-amber-400">
                {w}
              </li>
            ))}
          </ul>
        )}
        {saveError && <p className="text-xs text-rose-600 dark:text-rose-400">{saveError}</p>}

        {prof && (
          <div className="flex items-center justify-between gap-3 border-t border-border pt-3">
            <span className="text-xs text-muted-foreground">
              {confirmed
                ? "Confirmed — host scenes use this studio as set here."
                : `Classified by ${prof.review?.by ?? "—"}; not yet confirmed.`}
            </span>
            <Button
              size="sm"
              variant={confirmed ? "outline" : "default"}
              disabled={saving || !v?.ok}
              onClick={() => save({ confirm: !confirmed })}
            >
              {confirmed ? "Undo confirmation" : "Confirm studio"}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_1fr] items-center gap-3 text-sm">
      <div>
        <div className="text-muted-foreground">{label}</div>
        {hint && <div className="text-[11px] leading-4 text-muted-foreground">{hint}</div>}
      </div>
      <div className="min-w-0">{children}</div>
    </div>
  );
}
