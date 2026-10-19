"use client";

import { useState } from "react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { HostLookItem, HostOutfitItem } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const STATUS_VARIANT = { approved: "success", rejected: "danger", generated: "info", requested: "warning" } as const;

/** One look as a thumbnail with its state and the next step the owner can take. */
function LookTile({ look, busy, onAct }: { look: HostLookItem; busy: boolean; onAct: (fn: () => Promise<unknown>) => void }) {
  return (
    <li className="w-44 space-y-1 rounded-md border border-border p-1.5" data-testid={`look-${look.id}`}>
      {look.image_path ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={apiFileUrl(`/api/host-looks/${look.id}/image`)}
          alt={`${look.stance.toLowerCase()} ${look.camera} ${look.framing}`}
          className="aspect-video w-full rounded object-cover"
        />
      ) : (
        <div className="flex aspect-video w-full items-center justify-center rounded bg-muted text-[11px] text-muted-foreground">
          no picture yet
        </div>
      )}
      <div className="flex flex-wrap items-center gap-1">
        <Badge variant="outline">{look.framing}</Badge>
        <Badge variant={STATUS_VARIANT[look.status]}>{look.status}</Badge>
        {look.heygen_look_id && <Badge variant="info">HeyGen</Badge>}
      </div>
      <div className="flex flex-wrap gap-1">
        {look.status === "requested" && (
          <Button size="sm" variant="secondary" loading={busy} onClick={() => onAct(() => api.generateHostLook(look.id))}>
            Generate
          </Button>
        )}
        {look.status === "generated" && (
          <>
            <Button size="sm" loading={busy} onClick={() => onAct(() => api.approveHostLook(look.id))}>
              Approve
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onAct(() => api.rejectHostLook(look.id, "not right"))}>
              Reject
            </Button>
          </>
        )}
        {look.status === "approved" && !look.heygen_look_id && (
          <Button size="sm" variant="secondary" loading={busy} onClick={() => onAct(() => api.uploadHostLook(look.id))}>
            Upload to HeyGen
          </Button>
        )}
        {look.status === "approved" && (
          <Button size="sm" variant="ghost" onClick={() => onAct(() => api.rejectHostLook(look.id, "withdrawn"))}>
            Reject
          </Button>
        )}
        {look.status === "rejected" && (
          <Button size="sm" variant="ghost" onClick={() => onAct(() => api.regenerateHostLook(look.id, "keep the studio exactly as in the reference"))}>
            Regenerate
          </Button>
        )}
      </div>
    </li>
  );
}

/** The looks of an outfit grouped by sitting / standing and camera. */
function LookGroups({ outfit, busy, onAct }: { outfit: HostOutfitItem; busy: number | null; onAct: (id: number, fn: () => Promise<unknown>) => void }) {
  const groups = new Map<string, HostLookItem[]>();
  for (const l of outfit.looks) {
    const key = `${l.stance === "STANDING" ? "Standing" : "Seated"} · ${l.camera}`;
    groups.set(key, [...(groups.get(key) ?? []), l]);
  }
  if (groups.size === 0) return <p className="text-muted-foreground">No looks yet — request the pose set.</p>;
  return (
    <div className="space-y-2">
      {[...groups].map(([key, looks]) => (
        <div key={key}>
          <p className="mb-1 font-medium">{key}</p>
          <ul className="flex flex-wrap gap-2">
            {looks.map((l) => (
              <LookTile key={l.id} look={l} busy={busy === l.id} onAct={(fn) => onAct(l.id, fn)} />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** The outfit of this video (one case, one language): the same in every host scene. Its looks are
 *  requested here (the standard pose set in one click), generated one picture at a time, and
 *  approved by the owner — only approved looks of the selected outfit are ever used. */
export function HostOutfitsPanel({ caseId, language }: { caseId: number; language: string }) {
  const [tick, setTick] = useState(0);
  const { data: outfits } = useApi(() => api.outfits(caseId, language), [caseId, language, tick], { keepPrevious: true });
  const { data: registry } = useApi(() => api.outfitRegistry(), [], { keepPrevious: true });
  const [ref, setRef] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState<number | "new" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const choices = (registry ?? []).filter((o) => o.languages.includes(language));

  async function act(id: number | "new", fn: () => Promise<unknown>) {
    setBusy(id);
    setError(null);
    setNotice(null);
    try {
      const result = await fn();
      const skipped = (result as { skipped?: { label: string; why: string }[] } | undefined)?.skipped;
      if (skipped?.length) setNotice(skipped.map((s) => `${s.label}: ${s.why}`).join("; "));
      setTick((t) => t + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const selected = (outfits ?? []).find((o) => o.status === "selected");

  return (
    <Card data-testid="host-outfits">
      <CardHeader>
        <CardTitle className="text-sm">Outfit of this video ({language})</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 p-3 text-xs">
        <p className="text-muted-foreground">
          One outfit per case and language, the same in every host scene. Its looks (sitting or standing, camera,
          framing) are what the stage director chooses from; the app maps the plan to the best approved look of
          this outfit only. If none is approved the avatar step stops and tells you what to approve.
        </p>
        {(outfits ?? []).map((o) => (
          <div key={o.id} className="space-y-2 rounded-md border border-border p-2.5" data-testid={`outfit-${o.id}`}>
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-medium">{o.name}</span>
              {o.outfit_ref && <Badge variant="outline">{o.outfit_ref}</Badge>}
              <Badge variant={o.status === "selected" ? "success" : "default"}>{o.status}</Badge>
              <Badge variant="outline">{o.approved} approved</Badge>
              {!o.studio.STANDING && <Badge variant="warning">studio has no standing area</Badge>}
              {o.status !== "selected" && (
                <Button size="sm" variant="secondary" loading={busy === o.id} onClick={() => act(o.id, () => api.selectOutfit(o.id))}>
                  Use for this video
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => act(o.id, () => api.retireOutfit(o.id))}>
                Retire
              </Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" loading={busy === o.id} onClick={() => act(o.id, () => api.requestPoseSet(o.id))}>
                Request pose set
              </Button>
              <span className="text-muted-foreground">
                front wide + medium seated, left 45 seated, standing front, side view — requested, not generated.
              </span>
            </div>
            <LookGroups outfit={o} busy={typeof busy === "number" ? busy : null} onAct={(id, fn) => act(id, fn)} />
            {o.to_approve.length > 0 && (
              <div data-testid={`to-approve-${o.id}`}>
                <p className="font-medium">Still to do for this outfit</p>
                <ul className="list-disc pl-4 text-muted-foreground">
                  {o.to_approve.map((t) => <li key={t}>{t}</li>)}
                </ul>
              </div>
            )}
          </div>
        ))}
        {(outfits ?? []).length === 0 && <p className="text-muted-foreground">No outfit for this video yet.</p>}
        {!selected && (outfits ?? []).length > 0 && (
          <p className="text-amber-700 dark:text-amber-400">No outfit is selected: the avatar step cannot start.</p>
        )}
        <div className="flex flex-wrap gap-1.5">
          <select className="rounded-md border border-border bg-background px-2 py-1" value={ref} aria-label="Outfit"
                  onChange={(e) => { setRef(e.target.value); if (!name) setName(choices.find((c) => c.id === e.target.value)?.label ?? ""); }}>
            <option value="">choose an outfit…</option>
            {choices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select>
          <input className="min-w-40 rounded-md border border-border bg-background px-2 py-1" placeholder="name" value={name}
                 onChange={(e) => setName(e.target.value)} aria-label="Outfit name" />
          <Button size="sm" disabled={!ref || !name.trim()} loading={busy === "new"}
                  onClick={() => act("new", () => api.createOutfit(caseId, { language, name: name.trim(), outfit_ref: ref }))}>
            Create outfit
          </Button>
        </div>
        {notice && <p className="text-amber-700 dark:text-amber-400">{notice}</p>}
        {error && <p className="text-rose-600 dark:text-rose-400">{error}</p>}
      </CardContent>
    </Card>
  );
}
