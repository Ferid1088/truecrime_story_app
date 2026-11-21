"use client";

import { useState } from "react";
import { apiFileUrl, coverPhotosApi, coverShapesApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { InlineAlert, type LanguageTabProps } from "../shared";

/** The covers of a film: the film's own and the five Shorts'. */
const SLOTS = ["long", "C1", "C2", "C3", "C4", "C5"] as const;
const SLOT_LABEL: Record<string, string> = { long: "Film cover", C1: "Short 1", C2: "Short 2", C3: "Short 3", C4: "Short 4", C5: "Short 5" };
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The photographs of the people of the case (the victim first): each needs the owner's approval before a cover may show it; one of them can be made the film's cover photograph. */
export function PhotosTab({ caseId, language, refreshKey }: LanguageTabProps) {
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const { data, error, loading, refetch } = useApi(() => coverShapesApi.overview(caseId, language, "long"), [caseId, language, refreshKey, tick], { keepPrevious: true });
  const finished = useApi(() => coverPhotosApi.status(caseId), [caseId, tick], { keepPrevious: true });
  if (error && !data) return <ErrorState message={error} onRetry={refetch} />;
  if (!data) return <Skeleton className="h-40 w-full" />;
  const act = async (work: () => Promise<string>) => {
    setBusy(true); setNote(null);
    try { setNote({ tone: "ok", text: await work() }); } catch (e) { setNote({ tone: "bad", text: message(e) }); } finally { setBusy(false); setTick((t) => t + 1); }
  };
  const person = data.people[0];
  const approved = (person?.photos ?? []).filter((p) => p.authorized);
  return (
    <div className="space-y-4" data-testid="photos-tab">
      <InlineAlert tone="warning">{data.rights_note}</InlineAlert>
      {note && (note.tone === "ok" ? <p className="text-sm text-emerald-600 dark:text-emerald-400">{note.text}</p> : <InlineAlert tone="error">{note.text}</InlineAlert>)}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-3 pt-4 text-sm" data-testid="photos-finished">
          {finished.data?.finished ? (
            <>
              <Badge variant="success">finished</Badge>
              <span>You finished the cover photographs ({finished.data.photo_ids.length} approved).</span>
              <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void act(async () => { await coverPhotosApi.withdraw(caseId); return "The photographs are open again."; })}>Open again</Button>
            </>
          ) : (
            <>
              <Badge variant="warning">not finished</Badge>
              <span>Approve the photographs you want, then tell the system you are finished: it makes every cover with them.</span>
              <Button type="button" size="sm" disabled={busy || approved.length === 0} onClick={() => void act(async () => { const r = await coverPhotosApi.finish(caseId); return `Finished: ${r.made ?? 0} covers made${r.skipped?.length ? `, ${r.skipped.length} skipped` : ""}.`; })}>I am finished with the photographs</Button>
            </>
          )}
        </CardContent>
      </Card>
      {data.people.length === 0 && <EmptyState title="No photographs of people yet" description="The story has no photograph of a person." />}
      {data.people.map((p) => (
        <Card key={p.entity_key}>
          <CardHeader><CardTitle>{p.name} <span className="text-sm font-normal text-muted-foreground">· {p.photos.filter((x) => x.authorized).length} of {p.photos.length} approved</span></CardTitle></CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-3">
              {p.photos.map((ph) => (
                <figure key={ph.asset_id} className="w-44 space-y-1 text-xs" data-testid={`photo-${ph.asset_id}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element -- served by our own API */}
                  <img src={apiFileUrl(ph.image_url)} alt={`${p.name} ${ph.code}`} className="aspect-[4/5] w-full rounded border border-border object-cover" loading="lazy" />
                  <figcaption className="space-y-1">
                    <Badge variant={ph.authorized ? "success" : "warning"}>{ph.authorized ? "approved" : "not approved"}</Badge>
                    <span className="block text-muted-foreground">{ph.code} · {ph.width}×{ph.height} · {ph.source ?? "unknown source"}</span>
                    {!ph.authorized && ph.rights_status === "editorial_review_required" && (
                      <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void act(async () => { await coverShapesApi.approveEditorial(ph.asset_id, "owner"); return `Photograph ${ph.code} approved by the owner.`; })}>Approve this photograph</Button>
                    )}
                    {ph.authorized && (
                      <Button type="button" size="sm" disabled={busy} onClick={() => void act(async () => { await coverShapesApi.render(caseId, language, "long", { shapes: ["film"], subject_entity: p.entity_key, photo_asset_id: ph.asset_id }); return `The film cover was made with photograph ${ph.code}.`; })}>Use for the film cover</Button>
                    )}
                  </figcaption>
                </figure>
              ))}
            </div>
          </CardContent>
        </Card>
      ))}
      {person && (
        <Card>
          <CardHeader><CardTitle>Photograph of each cover</CardTitle></CardHeader>
          <CardContent>
            {approved.length === 0 ? <p className="text-sm text-muted-foreground">Approve at least one photograph: the covers use only approved ones.</p> : (
              <div className="flex flex-wrap gap-3" data-testid="photo-slots">
                {SLOTS.map((slot, i) => { const ph = approved[i % approved.length]; return (
                  <figure key={slot} className="w-24 space-y-1 text-xs">
                    {/* eslint-disable-next-line @next/next/no-img-element -- served by our own API */}
                    <img src={apiFileUrl(ph.image_url)} alt={`${SLOT_LABEL[slot]}: ${ph.code}`} className="aspect-[4/5] w-full rounded border border-border object-cover" loading="lazy" />
                    <figcaption className="text-muted-foreground">{SLOT_LABEL[slot]} · {ph.code}</figcaption>
                  </figure>
                ); })}
              </div>
            )}
            {approved.length > 0 && approved.length < SLOTS.length && <p className="mt-2 text-xs text-muted-foreground">Only {approved.length} approved: photographs repeat in turn until every cover has one.</p>}
          </CardContent>
        </Card>
      )}
      {loading && <p className="text-xs text-muted-foreground">Refreshing…</p>}
    </div>
  );
}
