"use client";

import { useState } from "react";
import { Shirt } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import type { HostOutfitOption, HostScene } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { InlineAlert } from "../shared";

const POSES = ["sitting", "standing"];
const usable = (photo: { stance: string }) => POSES.includes(photo.stance);

interface ChooserProps {
  /** Who gets the outfit: "the English host" or "scene S1". */
  subject: string;
  options: HostOutfitOption[];
  avatarEnabled: boolean;
  /** How many host videos a new outfit would make again (0: none yet). */
  videos: number;
  /** What "create the host" replaces, in the dialog's own words. */
  videoNoun: string;
  /** Saves the choice; resolves with the text to show, rejects with the reason. */
  onSave: (optionKey: string, itemId: number, createHost: boolean) => Promise<string>;
  onClose: () => void;
}

/** Pick an outfit, pick one of its photos, then confirm: nothing is saved before the owner says yes. */
function OutfitChooser({ subject, options, avatarEnabled, videos, videoNoun, onSave, onClose }: ChooserProps) {
  const [outfitKey, setOutfitKey] = useState<string | null>(null);
  const [photoId, setPhotoId] = useState<number | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [createHost, setCreateHost] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const outfit = options.find((o) => o.key === outfitKey);
  const photo = outfit?.photos.find((p) => p.id === photoId);
  const canCreate = avatarEnabled && videos > 0;

  const save = async () => {
    if (!outfit || !photo) return;
    setBusy(true);
    setError(null);
    try {
      await onSave(outfit.key, photo.id, createHost && canCreate);
      setConfirming(false);
      onClose();
    } catch (e) {
      setConfirming(false);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      {error && <InlineAlert tone="error">{error}</InlineAlert>}
      <div>
        <p className="mb-1.5 text-xs uppercase tracking-wide text-muted-foreground">1. Outfit</p>
        <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Outfit">
          {options.map((o) => (
            <Button
              key={o.key}
              size="sm"
              role="radio"
              aria-checked={o.key === outfitKey}
              variant={o.key === outfitKey ? "default" : "outline"}
              onClick={() => { setOutfitKey(o.key); setPhotoId(null); }}
            >
              {o.name}{o.selected ? " (now)" : ""}
            </Button>
          ))}
        </div>
      </div>
      {outfit && (
        <div>
          <p className="mb-1.5 text-xs uppercase tracking-wide text-muted-foreground">2. One photo of {outfit.name}</p>
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
                <span className="block text-muted-foreground">
                  {usable(p) ? p.stance : "pose not recognised"}{p.selected ? " · in use" : ""}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" disabled={!outfit || !photo || busy} onClick={() => setConfirming(true)}>
          Use this outfit and photo
        </Button>
        <Button size="sm" variant="ghost" onClick={onClose}>Cancel</Button>
      </div>

      <Dialog
        open={confirming && !!outfit && !!photo}
        onClose={() => setConfirming(false)}
        title="Are you sure?"
        description={`${subject} will wear ${outfit?.name ?? ""} with the photo "${photo?.situation ?? ""}".`}
      >
        <div className="space-y-3 text-sm">
          {photo && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={apiFileUrl(`/api/outfit-library/items/${photo.id}/image`)} alt={photo.situation}
                 className="mx-auto max-h-64 rounded border border-border object-contain" />
          )}
          <p>I approve it: the outfit and photo are saved for {subject} (no image is generated).</p>
          {canCreate ? (
            <label className="flex cursor-pointer items-start gap-2 text-xs">
              <input type="checkbox" checked={createHost} onChange={(e) => setCreateHost(e.target.checked)} className="mt-0.5" />
              <span>
                Create the host now: {videos} {videoNoun}{videos === 1 ? "" : "s"} made again with this outfit (HeyGen
                credits are used; a video already made with another outfit is replaced).
              </span>
            </label>
          ) : (
            <p className="text-xs text-muted-foreground">
              {avatarEnabled ? "No host video is made yet: the host is created with this outfit when it is made."
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
    </div>
  );
}

/** The host's outfit for the whole language: the scenes that have no outfit of their own wear it. */
export function HostOutfitPanel({ caseId, language, onChanged }: { caseId: number; language: string; onChanged: () => void }) {
  const { data, loading, error, refetch } = useApi(() => api.hostOutfitOptions(caseId, language), [caseId, language]);
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const current = data?.options.find((o) => o.selected);
  const videos = (data?.scenes ?? []).length;

  const save = async (optionKey: string, itemId: number, createHost: boolean) => {
    const done = await api.chooseHostOutfit(caseId, { language, option_key: optionKey, item_id: itemId, create_host: createHost });
    const made = done.started.length;
    const text = `Saved: ${done.outfit.name} is the ${langLabel(language)} host's outfit (scenes with an outfit of their own keep it).` +
      (made ? ` ${made} host video${made === 1 ? " is" : "s are"} being made with it.` : "");
    setMessage(text);
    refetch();
    onChanged();
    return text;
  };

  return (
    <Card data-testid="host-outfit-panel">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Shirt className="size-4" /> Host outfit · {langLabel(language)} · all scenes
        </CardTitle>
        <div className="flex flex-wrap items-center gap-1.5 text-xs">
          {loading && !data ? <Skeleton className="h-5 w-40" /> : current ? (
            <>
              <Badge variant="success">{current.name}</Badge>
              {current.photos.filter((p) => p.selected).map((p) => <Badge key={p.id} variant="outline">{p.situation}</Badge>)}
            </>
          ) : <Badge variant="warning">no outfit chosen yet</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && !data && <InlineAlert tone="error">{error}</InlineAlert>}
        {message && <p role="status" className="text-xs text-emerald-700">{message}</p>}
        <p className="text-xs text-muted-foreground">
          This is the outfit of every scene that has none of its own. Each scene below can wear a different outfit.
        </p>
        {open && data ? (
          <OutfitChooser
            subject={`the ${langLabel(language)} host`}
            options={data.options}
            avatarEnabled={data.avatar_enabled}
            videos={videos}
            videoNoun="host video"
            onSave={save}
            onClose={() => setOpen(false)}
          />
        ) : (
          <Button size="sm" variant="secondary" onClick={() => { setMessage(null); setOpen(true); }} disabled={!data}>
            {current ? "Change outfit for all scenes" : "Choose outfit"}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}

/** The outfit of one host scene, chosen for it alone. */
export function SceneOutfit({ scene, onChanged }: { scene: HostScene; onChanged: () => void }) {
  const { data, refetch } = useApi(() => api.hostSceneOutfit(scene.id), [scene.id, scene.status, scene.chosen_outfit_id]);
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async (optionKey: string, itemId: number, createHost: boolean) => {
    const done = await api.chooseSceneOutfit(scene.id, { option_key: optionKey, item_id: itemId, create_host: createHost });
    const text = `Saved: ${scene.host_segment_id} wears ${done.outfit?.name ?? ""}.` +
      (done.started ? " Its host video is being made." : data?.video_made ? " Its video still shows the previous outfit until it is made again." : "");
    setMessage(text);
    refetch();
    onChanged();
    return text;
  };

  const back = async () => {
    setBusy(true);
    try {
      await api.chooseSceneOutfit(scene.id, { option_key: null, create_host: false });
      setMessage(`${scene.host_segment_id} wears the video's outfit again.`);
      refetch();
      onChanged();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const locked = scene.running || !!data?.running;
  return (
    <div className="space-y-2 text-xs" data-testid={`scene-outfit-${scene.id}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium">Outfit</span>
        {data?.outfit ? (
          <>
            <Badge variant="outline">{data.outfit.name}</Badge>
            <Badge variant={data.outfit.pinned ? "info" : "default"}>
              {data.outfit.pinned ? "chosen for this scene" : "the video's outfit"}
            </Badge>
          </>
        ) : <span className="text-muted-foreground">no outfit chosen yet</span>}
        {!open && (
          <Button size="sm" variant="outline" disabled={!data || locked} onClick={() => { setMessage(null); setOpen(true); }}>
            Change outfit for {scene.host_segment_id}
          </Button>
        )}
        {data?.outfit?.pinned && !open && (
          <Button size="sm" variant="ghost" disabled={busy || locked} onClick={back}>Use the video&apos;s outfit</Button>
        )}
      </div>
      {message && <p role="status" className="text-emerald-700">{message}</p>}
      {open && data && (
        <OutfitChooser
          subject={`scene ${scene.host_segment_id}`}
          options={data.options}
          avatarEnabled={data.avatar_enabled}
          videos={1}
          videoNoun="host video"
          onSave={save}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}
