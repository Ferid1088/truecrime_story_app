"use client";

import { useState } from "react";
import { Check, ChevronLeft, X } from "lucide-react";
import Link from "next/link";

export default function ShortFormReviewPage() {
  const [status, setStatus] = useState("Pending review");
  const [caption, setCaption] = useState("The night the signal stopped");
  const [cta, setCta] = useState("Watch the full evidence-led story.");
  const [destination, setDestination] = useState("episode://pilot");

  return (
    <main className="mx-auto max-w-6xl px-6 py-8">
      <div className="mb-6 flex items-center justify-between gap-4">
        <div>
          <Link href="/short-form" className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ChevronLeft className="h-4 w-4" /> Short-Form</Link>
          <h1 className="text-2xl font-semibold tracking-tight">Review EN YouTube Short</h1>
          <p className="mt-1 text-sm text-muted-foreground">Pilot export / 1080 x 1920 / 8 seconds</p>
        </div>
        <span className="rounded border px-3 py-1 text-sm">{status}</span>
      </div>
      <div className="grid gap-6 lg:grid-cols-[minmax(280px,360px)_1fr]">
        <section className="flex min-h-[620px] items-center justify-center bg-muted/40 p-6">
          <div className="relative aspect-[9/16] w-full max-w-[300px] overflow-hidden bg-[#0e131b] text-white shadow-lg">
            <div className="absolute inset-5 border border-[#d3a44e]" />
            <div className="absolute inset-x-8 top-12 text-xs tracking-widest text-[#d3a44e]">TRUECRIME / SHORT</div>
            <div className="absolute inset-x-8 top-1/2 -translate-y-1/2 text-center text-2xl font-semibold">The night the signal stopped</div>
            <div className="absolute inset-x-8 bottom-24 text-center text-sm">AI-assisted narration</div>
          </div>
        </section>
        <section className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm font-medium">Caption<input aria-label="Caption" value={caption} onChange={(e) => setCaption(e.target.value)} className="mt-2 block w-full rounded border bg-background px-3 py-2 font-normal" /></label>
            <label className="text-sm font-medium">Destination<input aria-label="Destination" value={destination} onChange={(e) => setDestination(e.target.value)} className="mt-2 block w-full rounded border bg-background px-3 py-2 font-normal" /></label>
          </div>
          <label className="block text-sm font-medium">CTA<textarea aria-label="CTA" value={cta} onChange={(e) => setCta(e.target.value)} className="mt-2 block min-h-24 w-full rounded border bg-background px-3 py-2 font-normal" /></label>
          <div className="border-t pt-5 text-sm"><div className="mb-3 font-medium">Export checks</div><div className="space-y-2 text-muted-foreground"><div>1080 x 1920 vertical frame</div><div>Timed subtitle track included</div><div>AI disclosure metadata present</div><div>Rights status: cleared</div></div></div>
          <div className="flex flex-wrap gap-3"><button type="button" onClick={() => setStatus("Approved")} className="inline-flex items-center gap-2 rounded bg-primary px-4 py-2 text-primary-foreground"><Check className="h-4 w-4" /> Approve</button><button type="button" onClick={() => setStatus("Rejected")} className="inline-flex items-center gap-2 rounded border px-4 py-2"><X className="h-4 w-4" /> Reject</button></div>
        </section>
      </div>
    </main>
  );
}
