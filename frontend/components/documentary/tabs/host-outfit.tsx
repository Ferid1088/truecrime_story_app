"use client";

import { useState } from "react";
import { Shirt } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import type { HostOutfitOption, HostOutfitResult } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { InlineAlert } from "../shared";

const POSES = ["sitting", "standing"];
const usable = (photo: { stance: string }) => POSES.includes(photo.stance);

/** The host's outfit for this language: pick an outfit, pick one of its photos, confirm - the choice is saved and
 * approved as the host's look, and the host videos of the language can be made with it. */
export function HostOutfitPanel({ caseId, language, onChanged }: { caseId: number; language: string; onChanged: () => void }) {
  const { data, loading, error, refetch } = useApi(() => api.hostOutfitOptions(caseId, language), [caseId, language]);
  const [open, setOpen] = useState(false);
  const [outfitKey, setOutfitKey] = useState<string | null>(null);
  const [photoId, setPhotoId] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [createHost, setCreateHost] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  const current = data?.options.find((o) => o.selected);
  const outfit: HostOutfitOption | undefined = data?.options.find((o) => o.key === outfitKey);
  const photo = outfit?.photos.find((p) => p.id === photoId);
  const videos = (data?.scenes ?? []).length;
  const canCreate = !!data?.avatar_enabled && videos > 0;

  const choose = (key: string) => {
    setOutfitKey(key);
    setPhotoId(null);
  };

  const save = async () => {
    if (!outfit || !photo) return;
    setBusy(true);
    setMessage(null);
    try {
      const done: HostOutfitResult = await api.chooseHostOutfit(caseId, {
        language, option_key: outfit.key, item_id: photo.id, create_host: createHost && canCreate,
      });
      const made = done.started.length;
      setMessage({
        tone: "ok",
        text: `Saved: ${done.outfit.name} is the ${langLabel(language)} host's outfit.` +
          (made ? ` ${made} host video${made === 1 ? " is" : "s are"} being made with it.` : "") +
          (done.skipped.length ? ` ${done.skipped.length} look(s) kept: ${done.skipped.map((s) => s.why).join("; ")}.` : ""),
      });
      setConfirming(false);
      setOpen(false);
      setOutfitKey(null);
      setPhotoId(null);
      refetch();
      onChanged();
    } catch (e) {
      setConfirming(false);
      setMessage({ tone: "error", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card data-testid="host-outfit-panel">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Shirt className="size-4" /> Host outfit · {langLabel(language)}
        </CardTitle>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {loading && !data ? <Skeleton className="h-5 w-40" /> : current ? (
            <>
              <Badge variant="success">{current.name}</Badge>
              {current.photos.filter((p) => p.selected).map((p) => (
                <Badge key={p.id} variant="outline">{p.situation}</Badge>
              ))}
            </>
          ) : (
            <Badge variant="warning">no outfit chosen yet</Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && !data && <InlineAlert tone="error">{error}</InlineAlert>}
        {message && (message.tone === "error"
          ? <InlineAlert tone="error">{message.text}</InlineAlert>
          : <p role="status" className="text-xs text-emerald-700">{message.text}</p>)}
        {!open ? (
          <Button size="sm" variant="secondary" onClick={() => setOpen(true)} disabled={!data}>
            {current ? "Change outfit" : "Choose outfit"}
          </Button>
        ) : (
          <div className="space-y-3">
            <div>
              <p className="mb-1.5 text-xs uppercase tracking-wide text-muted-foreground">1. Outfit</p>
              <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Outfit">
                {(data?.options ?? []).map((o) => (
                  <Button
                    key={o.key}
                    size="sm"
                    role="radio"
                    aria-checked={o.key === outfitKey}
                    variant={o.key === outfitKey ? "default" : "outline"}
                    onClick={() => choose(o.key)}
                  >
                    {o.name}{o.selected ? " (now)" : ""}
                  </Button>
                ))}
              </div>
            </div>
            {outfit && (
              <div>
                <p className="mb-1.5 text-xs uppercase tracking-wide text-muted-foreground">
                  2. One photo of {outfit.name}
                </p>
                {outfit.photos.filter((p) => p.status === "available").length === 0 && (
                  <p className="text-xs text-muted-foreground">This outfit has no photos available.</p>
                )}
                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6" role="radiogroup" aria-label="Photo">
                  {outfit.photos.filter((p) => p.status === "available").map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      role="radio"
                      aria-checked={p.id === photoId}
                      disabled={!usable(p)}
                      title={usable(p) ? undefined : "The pose of this photo is not sitting or standing, so it cannot be a host look"}
                      onClick={() => setPhotoId(p.id)}
                      className={`rounded border p-1 text-left text-xs disabled:cursor-not-allowed disabled:opacity-40 ${
                        p.id === photoId ? "cursor-pointer border-primary ring-2 ring-primary/40" : "cursor-pointer border-border hover:bg-muted/50"
                      }`}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={apiFileUrl(`/api/outfit-library/items/${p.id}/image`)} alt={p.situation} loading="lazy"
                           className="aspect-[3/4] w-full rounded object-cover" />
                      <span className="mt-1 block truncate">{p.situation}</span>
                      <span className="block text-muted-foreground">{usable(p) ? p.stance : "pose not recognised"}{p.selected ? " · in use" : ""}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" disabled={!outfit || !photo || busy} onClick={() => setConfirming(true)}>
                Use this outfit and photo
              </Button>
              <Button size="sm" variant="ghost" onClick={() => { setOpen(false); setOutfitKey(null); setPhotoId(null); }}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </CardContent>

      <Dialog
        open={confirming && !!outfit && !!photo}
        onClose={() => setConfirming(false)}
        title="Are you sure?"
        description={`The ${langLabel(language)} host will wear ${outfit?.name ?? ""} with the photo "${photo?.situation ?? ""}".`}
      >
        <div className="space-y-3 text-sm">
          {photo && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={apiFileUrl(`/api/outfit-library/items/${photo.id}/image`)} alt={photo.situation}
                 className="mx-auto max-h-64 rounded border border-border object-contain" />
          )}
          <p>
            I approve it: the outfit and photo are saved as this film&apos;s {langLabel(language)} host look
            (no image is generated).
          </p>
          {canCreate ? (
            <label className="flex cursor-pointer items-start gap-2 text-xs">
              <input type="checkbox" checked={createHost} onChange={(e) => setCreateHost(e.target.checked)}
                     className="mt-0.5" />
              <span>
                Create the host now: {videos} host video{videos === 1 ? "" : "s"} of this language are made again with
                this outfit (HeyGen credits are used; videos already made with another outfit are replaced).
              </span>
            </label>
          ) : (
            <p className="text-xs text-muted-foreground">
              {data?.avatar_enabled ? "There are no host scenes yet: the host is created with this outfit when they are made."
                : "Avatar generation is off (config avatar.enabled): the host is created with this outfit once it is on."}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={busy}>No, go back</Button>
            <Button size="sm" onClick={save} disabled={busy} data-testid="host-outfit-confirm">
              {busy ? "Saving..." : "Yes, approve"}
            </Button>
          </div>
        </div>
      </Dialog>
    </Card>
  );
}
