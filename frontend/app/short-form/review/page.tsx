"use client";

import { useState } from "react";
import { Check, ChevronLeft, X } from "lucide-react";
import Link from "next/link";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

type Render = { id: number; language: string; candidate_id: number; status: string; approval_state: string; caption_text: string; cta: string; video_url: string | null; cover_url: string | null; gate_results: Record<string, { passed?: boolean }>; };
type Workspace = { case: { id: number; title: string } | null; renders: Render[] };

export default function ShortFormReviewPage() {
  const { data, loading, error, refetch } = useApi(() => api.shortFormWorkspace() as Promise<Workspace>);
  const [caption, setCaption] = useState("");
  const [cta, setCta] = useState("");
  const [message, setMessage] = useState("");
  const selected = data?.renders[0];
  if (loading && !data) return <Skeleton className="h-[620px]" />;
  if (error && !data) return <p className="p-8 text-sm text-rose-700">{error}</p>;
  if (!data?.case || !selected) return <main className="mx-auto max-w-4xl px-6 py-12"><h1 className="text-2xl font-semibold">No rendered short to review</h1><p className="mt-2 text-sm text-muted-foreground">Open this page after a finished film has produced a language render.</p></main>;
  const fieldCaption = caption || selected.caption_text;
  const fieldCta = cta || selected.cta;
  const action = async (name: string, edit?: Record<string, string>) => { const preview = await api.shortFormRenderPreview(data.case!.id, selected.id, { action: name, edit }) as { preview_id: number }; await api.shortFormOperationApply(data.case!.id, preview.preview_id); setMessage(name === "approve" ? "Approved" : name === "reject" ? "Rejected" : "Updated"); setCaption(""); setCta(""); await refetch(); };
  return <main className="px-6 py-8"><div className="mb-6 flex items-center justify-between gap-4"><div><Link href="/short-form" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ChevronLeft className="h-4 w-4" /> Short-Form</Link><h1 className="text-2xl font-semibold tracking-tight">Review {selected.language.toUpperCase()} Short {selected.candidate_id}</h1><p className="mt-1 text-sm text-muted-foreground">{data.case.title} / 1080 × 1920 / {selected.status}</p></div><span className="rounded border px-3 py-1 text-sm">{selected.approval_state}</span></div><div className="grid gap-6 lg:grid-cols-[minmax(280px,360px)_1fr]"><section className="flex min-h-[620px] items-center justify-center bg-muted/40 p-6">{selected.video_url ? <video controls poster={selected.cover_url ? apiFileUrl(selected.cover_url) : undefined} className="aspect-[9/16] w-full max-w-[300px] bg-black" src={apiFileUrl(selected.video_url)} aria-label="Short video" /> : <p className="text-sm text-muted-foreground">Video unavailable</p>}</section><section className="space-y-6"><label className="block text-sm font-medium">Caption<input aria-label="Caption" value={fieldCaption} onChange={(event) => setCaption(event.target.value)} className="mt-2 block w-full rounded border bg-background px-3 py-2 font-normal" /></label><label className="block text-sm font-medium">CTA<textarea aria-label="CTA" value={fieldCta} onChange={(event) => setCta(event.target.value)} className="mt-2 block min-h-24 w-full rounded border bg-background px-3 py-2 font-normal" /></label><div className="border-t pt-5 text-sm"><div className="mb-3 font-medium">Export checks</div><div className="space-y-2 text-muted-foreground"><div>1080 × 1920 vertical frame</div><div>Burned-in caption plus canonical timed track</div><div>Language: {selected.language} · RTL layout applies for fa/ar</div><div>Visibility: private</div></div></div>{message && <p role="status" className="text-sm text-emerald-700">{message}</p>}<div className="flex flex-wrap gap-3"><Button type="button" onClick={() => void action("approve")}><Check /> Approve</Button><Button type="button" variant="outline" onClick={() => void action("edit", { caption: fieldCaption, cta: fieldCta })}>Save edit</Button><Button type="button" variant="ghost" onClick={() => void action("reject")}><X /> Reject</Button></div></section></div></main>;
}
