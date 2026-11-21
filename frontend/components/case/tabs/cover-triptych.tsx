"use client";

import { useState } from "react";
import { api, apiFileUrl, coverShapesApi, type CoverShapeKey, type CoverShapes, type CoverVariantView } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";

const DISABLED = "disabled:opacity-100 disabled:bg-muted disabled:text-muted-foreground disabled:border disabled:border-dashed disabled:border-border disabled:cursor-not-allowed";
/** The shapes a cover key has: the three of a Short, or the one 16:9 shape of the full film (key "long"). */
const shapesOf = (coverKey: string): CoverShapeKey[] => (coverKey === "long" ? ["film"] : ["reel", "feed", "grid"]);
const RATIO: Record<CoverShapeKey, string> = { reel: "9 / 16", feed: "4 / 5", grid: "1 / 1", film: "16 / 9" };
const STATUS_TONE = { approved: "success", proposed: "info", rejected: "default", superseded: "default" } as const;
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export type NewCover = { url: string; version: number; status: string };
export type NewCovers = Partial<Record<CoverShapeKey, NewCover>>;

/** The newest version of each of the three shapes made for a Short (empty while none was made); `refresh` changes when the review below made or rejected one. */
export function useNewCovers(caseId: number, language: string, coverKey: string, refresh: number): NewCovers {
  const { data } = useApi(() => coverShapesApi.overview(caseId, language, coverKey), [caseId, language, coverKey, refresh], { keepPrevious: true });
  const covers: NewCovers = {};
  if (data) for (const shape of shapesOf(coverKey)) { const newest = data.shapes[shape]?.newest; if (newest) covers[shape] = { url: img(newest), version: newest.version, status: newest.status }; }
  return covers;
}
const img = (v: CoverVariantView) => apiFileUrl(v.image_url);

/** A simplified mock-up of where the shape is seen. A preview, never Facebook's or Instagram's own rendering. */
function PhoneMockup({ variant }: { variant: CoverVariantView }) {
  const picture = (
    // eslint-disable-next-line @next/next/no-img-element -- served by our own API
    <img src={img(variant)} alt={`${variant.label} on a phone`} className="h-full w-full object-cover" />
  );
  return (
    <div className="mx-auto w-64 rounded-[2rem] border-4 border-neutral-700 bg-neutral-900 p-2" data-testid={`phone-${variant.shape}`}>
      <div className="overflow-hidden rounded-[1.4rem] bg-black">
        {variant.shape === "reel" && <div style={{ aspectRatio: "9 / 16" }}>{picture}</div>}
        {variant.shape === "feed" && (
          <div className="bg-neutral-900 pb-3 text-[10px] text-neutral-300">
            <div className="flex items-center gap-2 p-2"><span className="size-6 rounded-full bg-neutral-600" /><span>Your page · Sponsored look</span></div>
            <div style={{ aspectRatio: "4 / 5" }}>{picture}</div>
            <div className="space-y-1 px-2 pt-2"><div className="h-2 w-3/4 rounded bg-neutral-700" /><div className="h-2 w-1/2 rounded bg-neutral-700" /></div>
          </div>
        )}
        {variant.shape === "grid" && (
          <div className="bg-neutral-900 pb-2 text-[10px] text-neutral-300">
            <div className="flex items-center gap-2 p-2"><span className="size-8 rounded-full bg-neutral-600" /><div className="space-y-1"><div className="h-2 w-20 rounded bg-neutral-600" /><div className="h-2 w-12 rounded bg-neutral-700" /></div></div>
            <div className="grid grid-cols-3 gap-px">
              {Array.from({ length: 9 }, (_, i) => <div key={i} style={{ aspectRatio: "1 / 1" }} className="overflow-hidden bg-neutral-800">{i === 1 ? picture : null}</div>)}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ShapeColumn({ shape, data, busy, onZoom, onApprove, onReject, onRender }: {
  shape: CoverShapeKey; data: CoverShapes["shapes"][CoverShapeKey]; busy: boolean;
  onZoom: (v: CoverVariantView, mode: "full" | "phone") => void; onApprove: (v: CoverVariantView) => void; onReject: (v: CoverVariantView) => void; onRender: () => void;
}) {
  const shown = data.newest;
  const measured = shown?.evidence.measured;
  return (
    <div className="space-y-2" data-testid={`triptych-shape-${shape}`}>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="font-medium">{data.label}</span>
        {shown && <Badge variant={STATUS_TONE[shown.status]} data-testid={`triptych-status-${shape}`}>{shown.status} · v{shown.version}</Badge>}
        {shown && shown.status === "proposed" && data.approved && <Badge variant="warning">the approved v{data.approved.version} stays until you approve this</Badge>}
      </div>
      {shown ? (
        <button type="button" className="block w-full max-w-60 cursor-zoom-in overflow-hidden rounded border border-border bg-black" style={{ aspectRatio: RATIO[shape] }} onClick={() => onZoom(shown, "full")} aria-label={`Zoom ${data.label}`} data-testid={`triptych-image-${shape}`}>
          {/* eslint-disable-next-line @next/next/no-img-element -- served by our own API */}
          <img src={img(shown)} alt={`${data.label} cover, version ${shown.version}`} className="h-full w-full object-cover" />
        </button>
      ) : <div className="flex max-w-60 items-center justify-center rounded border border-dashed border-border text-xs text-muted-foreground" style={{ aspectRatio: RATIO[shape] }}>not rendered yet</div>}
      {shown && (
        <>
          {shown.problems.length > 0
            ? <ul className="list-disc space-y-0.5 pl-4 text-xs text-rose-700 dark:text-rose-400" data-testid={`triptych-problems-${shape}`}>{shown.problems.map((p) => <li key={p}>{p}</li>)}</ul>
            : <p className="text-xs text-emerald-700 dark:text-emerald-400" data-testid={`triptych-clean-${shape}`}>No problems found by the checks.</p>}
          {measured && <p className="text-xs text-muted-foreground">Measured: host sharpness {measured.host_sharpness}, title contrast {measured.title_contrast}, title {measured.title_px}px high. {shown.subject ? `Banner names ${shown.subject}${shown.photo_asset_id ? ", with a cleared photograph" : ", no photograph (none cleared)"}.` : "No person on it."}</p>}
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" className={DISABLED} disabled={busy || shown.status !== "proposed" || shown.problems.length > 0} onClick={() => onApprove(shown)} data-testid={`triptych-approve-${shape}`}>Approve</Button>
            <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy || shown.status !== "proposed"} onClick={() => onReject(shown)} data-testid={`triptych-reject-${shape}`}>Reject</Button>
            <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy} onClick={onRender} data-testid={`triptych-again-${shape}`}>Render again</Button>
            {shape !== "film" && <Button type="button" size="sm" variant="ghost" onClick={() => onZoom(shown, "phone")} data-testid={`triptych-phone-${shape}`}>Phone preview</Button>}
          </div>
        </>
      )}
    </div>
  );
}

function Panel({ caseId, language, coverKey, onChanged }: { caseId: number; language: string; coverKey: string; onChanged: () => void }) {
  const { data, error, refetch } = useApi(() => coverShapesApi.overview(caseId, language, coverKey), [caseId, language, coverKey], { keepPrevious: true });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [zoom, setZoom] = useState<{ variant: CoverVariantView; mode: "full" | "phone" } | null>(null);
  const [subject, setSubject] = useState("");
  const [photo, setPhoto] = useState("");
  const [highlight, setHighlight] = useState("");
  const [bannerName, setBannerName] = useState("");
  if (error && !data) return <p className="text-xs text-rose-700 dark:text-rose-400">{error}</p>;
  if (!data) return <p className="text-xs text-muted-foreground">Loading…</p>;
  const act = async (work: () => Promise<string>) => {
    setBusy(true); setNote(null);
    try { setNote({ tone: "ok", text: await work() }); } catch (e) { setNote({ tone: "bad", text: message(e) }); } finally { setBusy(false); refetch(); onChanged(); }
  };
  const render = (shapes?: CoverShapeKey[]) => act(async () => {
    await coverShapesApi.render(caseId, language, coverKey, { shapes, highlight: highlight.trim() || undefined, subject_entity: subject || undefined, subject_name: bannerName.trim() || data.shapes[shapes?.[0] ?? shapesOf(coverKey)[0]].newest?.subject || undefined, use_photo: photo !== "none", photo_asset_id: photo && photo !== "none" ? Number(photo) : undefined });
    return `New ${shapes ? shapes.join(", ") : shapesOf(coverKey).join(", ")} version${shapes && shapes.length === 1 ? "" : "s"} made in code: nothing was paid and nothing approved was changed.`;
  });
  const chosen = data.people.find((p) => p.entity_key === subject) ?? (subject === "" ? data.people[0] : undefined);
  return (
    <div className="space-y-4 text-xs" data-testid={`triptych-panel-${coverKey}`}>
      <p className="text-muted-foreground" data-testid={`triptych-hook-${coverKey}`}>
        Hook: <span className="font-medium text-foreground" dir="auto">{data.hook.title ?? "no title yet"}</span> ({data.hook.status ?? "none"}{data.hook.confidence != null ? `, certainty ${data.hook.confidence}` : ""}). These three shapes are reusable assets drawn in code from real parts; none of them is sent to a platform (see the table below).
      </p>
      {!data.plate.available && <p role="alert" className="rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1 text-amber-700 dark:text-amber-400" data-testid="triptych-plate-missing">{data.plate.problem} Nothing is rendered and no text is invented.</p>}
      <div className="flex flex-wrap items-end gap-3">
        <label className="font-medium">Person on the cover
          <Select aria-label="Person on the cover" value={subject} onChange={(e) => { setSubject(e.target.value); setPhoto(""); }} className="mt-1 w-56">
            <option value="">Most photographed{data.people[0] ? `: ${data.people[0].name}` : ""}</option>
            {data.people.map((p) => <option key={p.entity_key} value={p.entity_key}>{p.name}</option>)}
            <option value="none">No person (host and studio only)</option>
          </Select>
        </label>
        <label className="font-medium">Photograph
          <Select aria-label="Photograph" value={photo} onChange={(e) => setPhoto(e.target.value)} className="mt-1 w-48" disabled={subject === "none"}>
            <option value="">First cleared one (none if none is cleared)</option>
            {(chosen?.photos ?? []).filter((p) => p.authorized).map((p) => <option key={p.asset_id} value={p.asset_id}>{p.code}</option>)}
            <option value="none">No photograph</option>
          </Select>
        </label>
        <label className="font-medium">Name on the banner
          <input aria-label="Name on the banner" value={bannerName} onChange={(e) => setBannerName(e.target.value)} placeholder={data.shapes.reel.newest?.subject ?? chosen?.name ?? "the person's name"} dir="auto" disabled={subject === "none"} className="mt-1 block h-8 w-44 rounded-md border border-border bg-background px-2 text-sm font-normal" />
        </label>
        <label className="font-medium">Key word on the red stroke
          <input aria-label="Key word" value={highlight} onChange={(e) => setHighlight(e.target.value)} placeholder="the last word" dir="auto" className="mt-1 block h-8 w-44 rounded-md border border-border bg-background px-2 text-sm font-normal" />
        </label>
        <Button type="button" size="sm" className={DISABLED} disabled={busy || !data.can_render} onClick={() => void render()} data-testid={`triptych-render-${coverKey}`}>{busy ? "Working…" : "Render new versions (free)"}</Button>
      </div>
      {note && <p role="status" data-testid={`triptych-note-${coverKey}`} className={note.tone === "ok" ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"}>{note.text}</p>}
      <div className="flex flex-wrap items-start gap-6" data-testid={`triptych-shapes-${coverKey}`}>
        {shapesOf(coverKey).map((shape) => (
          <ShapeColumn key={shape} shape={shape} data={data.shapes[shape]} busy={busy}
            onZoom={(variant, mode) => setZoom({ variant, mode })} onReject={(v) => void act(async () => { await coverShapesApi.reject(v.id); return `${v.label} version ${v.version} rejected.`; })}
            onApprove={(v) => void act(async () => { await coverShapesApi.approve(v.id); return `${v.label} version ${v.version} approved. An older approved version of this shape is kept as superseded.`; })} onRender={() => void render([shape])} />
        ))}
      </div>
      <div className="space-y-2 rounded border border-border p-2" data-testid={`triptych-people-${coverKey}`}>
        <p className="font-medium">People and photographs</p>
        <p className="text-muted-foreground" data-testid="triptych-rights-note">{data.rights_note}</p>
        {data.people.length === 0 && <p className="text-muted-foreground">The story has no photographs of people yet.</p>}
        {data.people.map((p) => (
          <div key={p.entity_key} className="space-y-1">
            <p className="font-medium">{p.name} <span className="font-normal text-muted-foreground">· {p.photos.filter((x) => x.authorized).length} of {p.photos.length} photographs cleared</span></p>
            <div className="flex flex-wrap gap-3">
              {p.photos.map((ph) => (
                <figure key={ph.asset_id} className="w-36 space-y-1" data-testid={`triptych-photo-${ph.asset_id}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element -- served by our own API */}
                  <img src={apiFileUrl(ph.image_url)} alt={`${p.name} ${ph.code}`} className="aspect-[4/5] w-full rounded border border-border object-cover" loading="lazy" />
                  <figcaption className="space-y-1">
                    <Badge variant={ph.authorized ? "success" : "warning"}>{ph.authorized ? "authorized" : "not authorized"}</Badge>
                    <span className="block text-muted-foreground">{ph.code} · {ph.width}×{ph.height} · {ph.source ?? "unknown source"}</span>
                    <span className="block text-muted-foreground">{ph.why}</span>
                    {!ph.authorized && ph.rights_status === "editorial_review_required" && <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy} onClick={() => void act(async () => { await coverShapesApi.approveEditorial(ph.asset_id, "owner"); return `${ph.code} approved editorially. This is your judgment that it may be used for reporting, not copyright permission.`; })}>Approve editorially</Button>}
                    {ph.authorized && ph.editorial_approved_by && <Button type="button" size="sm" variant="ghost" className={DISABLED} disabled={busy} onClick={() => void act(async () => { await api.revokeEditorial(ph.asset_id); return `The editorial approval of ${ph.code} was withdrawn.`; })}>Withdraw approval</Button>}
                  </figcaption>
                </figure>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="overflow-x-auto" data-testid={`triptych-platforms-${coverKey}`}>
        <p className="mb-1 font-medium">Where each version is used</p>
        <table className="w-full text-left">
          <thead><tr className="text-muted-foreground"><th className="pr-3 font-medium">Platform</th><th className="pr-3 font-medium">Takes</th><th className="pr-3 font-medium">Uploaded now</th><th className="font-medium">Shapes</th></tr></thead>
          <tbody>
            {data.platforms.map((p) => (
              <tr key={p.destination} className="align-top"><td className="pr-3 py-1 font-medium">{p.destination}</td><td className="pr-3 py-1">{p.takes}</td><td className="pr-3 py-1">{p.uploaded_now}</td>
                <td className="py-1"><div className="flex flex-wrap gap-1">{shapesOf(coverKey).map((s) => <Badge key={s} variant={s === p.candidate_shape ? "info" : "default"}>{s}: {s === p.candidate_shape ? "candidate (not uploaded yet)" : "preview only"}</Badge>)}</div></td></tr>
            ))}
          </tbody>
        </table>
      </div>
      <Dialog open={zoom !== null} onClose={() => setZoom(null)} title={zoom ? `${zoom.variant.label}, version ${zoom.variant.version}${zoom.mode === "phone" ? ": phone preview" : ""}` : ""} description={zoom?.mode === "phone" ? "Preview only: a simplified mock-up, not how the platform really renders it." : "The rendered picture at full size."} className="max-w-3xl">
        {zoom && (zoom.mode === "phone" ? <PhoneMockup variant={zoom.variant} /> : (
          // eslint-disable-next-line @next/next/no-img-element -- served by our own API
          <img src={img(zoom.variant)} alt={`${zoom.variant.label} at full size`} data-testid="triptych-zoomed" className="mx-auto max-h-[75vh] w-auto rounded border border-border" />
        ))}
      </Dialog>
    </div>
  );
}

/** The shapes of a cover (three for a Short, one 16:9 for the film), behind a fold (nothing is fetched until it is opened). */
export function CoverTriptych({ caseId, language, coverKey, onChanged }: { caseId: number; language: string; coverKey: string; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Card data-testid={`triptych-${coverKey}`}>
      <CardHeader className="cursor-pointer" onClick={() => setOpen(!open)}>
        <CardTitle>{coverKey === "long" ? "Film cover: 16:9" : "Cover versions: reel 9:16, feed 4:5, grid 1:1"} <span className="text-xs font-normal text-muted-foreground">(preview and review; the platforms take one cover)</span></CardTitle>
        <Button type="button" size="sm" variant="outline" aria-expanded={open} data-testid={`triptych-open-${coverKey}`}>{open ? "Hide" : "Show"}</Button>
      </CardHeader>
      {open && <CardContent><Panel caseId={caseId} language={language} coverKey={coverKey} onChanged={onChanged} /></CardContent>}
    </Card>
  );
}
