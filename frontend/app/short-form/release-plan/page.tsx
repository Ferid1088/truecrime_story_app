"use client";

import { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ArrowLeft, CalendarDays, Check, CircleAlert, Save } from "lucide-react";
import { PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorState } from "@/components/state";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";

type Candidate = {
  id: number;
  stable_key: string;
  hook: string;
  status: string;
  included: boolean;
  languages: Array<{ language: string; render_id: number; render_status: string; approval_state: string; video_url: string | null }>;
};
type Slot = { slot_key: string; day: number; publish_at: string; candidate_id: number; short_number: number; language: string; platform: string; platform_label: string; render_id: number | null; render_status: string; channel_url: string | null; status: string };
type Document = { case_id: number; case_title: string; film_job_id: number; timezone: string; start_date: string; publish_time_local: string; review_language: string; can_publish: boolean; candidates: Candidate[]; slots: Slot[]; connected_accounts: number; required_accounts: number; publisher_status: string };
type PlanEnvelope = { id: number | null; status: string; document: Document; publish_ready?: boolean; publish_note?: string };

function slotDate(start: string, day: number) {
  const [year, month, date] = start.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, date + day - 1)).toISOString().slice(0, 10);
}

export default function ReleasePlanPage() {
  return <Suspense fallback={<Skeleton className="h-[620px]" />}><ReleasePlanContent /></Suspense>;
}

function ReleasePlanContent() {
  const params = useSearchParams();
  const caseId = Number(params.get("case"));
  if (!Number.isInteger(caseId) || caseId <= 0) {
    return <main className="mx-auto max-w-5xl space-y-5 px-6 py-8"><PageHeader title="Release plan" description="Open a finished episode from Short-Form to prepare its release plan." /><Link href="/short-form" className="text-sm underline">Back to Short-Form</Link></main>;
  }
  return <ReleasePlanEditor caseId={caseId} />;
}

function ReleasePlanEditor({ caseId }: { caseId: number }) {
  const { data, loading, error, refetch } = useApi(() => api.shortFormReleasePlan(caseId) as Promise<PlanEnvelope>, [caseId]);
  const [included, setIncluded] = useState<number[]>([]);
  const [startDate, setStartDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!data) return;
    setIncluded(data.document.candidates.filter((candidate) => candidate.included).map((candidate) => candidate.id));
    setStartDate(data.document.start_date);
  }, [data]);

  const selected = useMemo(() => new Set(included), [included]);
  const slots = useMemo(() => data?.document.slots.filter((slot) => selected.has(slot.candidate_id)) ?? [], [data, selected]);

  if (loading && !data) return <main className="mx-auto max-w-6xl px-6 py-8"><Skeleton className="h-[620px]" /></main>;
  if (error && !data) return <main className="mx-auto max-w-6xl px-6 py-8"><ErrorState message={error} onRetry={refetch} /></main>;
  if (!data) return null;

  const save = async (approve: boolean) => {
    setBusy(true); setMessage("");
    try {
      const saved = await api.saveShortFormReleasePlan(caseId, { included_candidate_ids: included, start_date: startDate }) as PlanEnvelope;
      if (approve && saved.id) {
        const result = await api.approveShortFormReleasePlan(caseId, saved.id) as PlanEnvelope;
        setMessage(result.publish_ready ? "برنامه تأیید شد." : "برنامه تأیید و ذخیره شد؛ اتصال و انتشار خودکار پلتفرم‌ها هنوز در اپ پیاده‌سازی نشده است.");
      } else {
        setMessage("پیش‌نویس برنامه ذخیره شد.");
      }
      await refetch();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally { setBusy(false); }
  };

  const doc = data.document;
  return <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
    <Link href={`/short-form?case=${caseId}`} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"><ArrowLeft className="size-4" /> Back to episode</Link>
    <PageHeader title="Episode release plan" description="One plan for the episode. Each checked short is scheduled across its ready languages and enabled platforms." actions={<span className="rounded border border-border px-3 py-1 text-xs">{data.status}</span>} />
    <section className="flex flex-wrap items-center justify-between gap-4 border-y border-border py-3 text-sm"><div><p className="font-medium">{doc.case_title}</p><p className="text-xs text-muted-foreground">Film job {doc.film_job_id} · Review language: {doc.review_language.toUpperCase()} · {doc.timezone}</p></div><label className="flex items-center gap-2"><CalendarDays className="size-4" /> Start date <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} className="rounded border border-border bg-background px-2 py-1" /></label></section>
    <section className="flex items-start gap-3 rounded-md border border-amber-500/40 bg-amber-50/50 p-4 text-sm dark:bg-amber-950/10"><CircleAlert className="mt-0.5 size-4 shrink-0 text-amber-700" /><div><p className="font-medium">The plan can be reviewed and saved; live publishing is not connected yet.</p><p className="mt-1 text-muted-foreground">Account IDs and the release calendar are configured in <code>config/social_distribution.json</code>. OAuth credentials are not stored in that file. The current publisher adapters do not upload.</p></div></section>
    <section className="space-y-3">
      <div><h2 className="text-lg font-semibold">Shorts in this campaign</h2><p className="text-sm text-muted-foreground">One checkbox controls all scheduled platforms and ready language versions for that short. Approve each selected short in {doc.review_language.toUpperCase()} from the Rendered Shorts tab before approving the campaign.</p></div>
      {!doc.candidates.length ? <p className="rounded border border-dashed p-6 text-sm text-muted-foreground">No rendered shorts are ready for a release plan yet.</p> : doc.candidates.map((candidate, index) => {
        const reference = candidate.languages.find((item) => item.language === doc.review_language) ?? candidate.languages[0];
        return <article key={candidate.id} className="grid gap-4 rounded-lg border border-border p-4 md:grid-cols-[minmax(0,1fr)_180px]">
          <div><label className="flex cursor-pointer items-start gap-3"><input type="checkbox" checked={selected.has(candidate.id)} onChange={(event) => setIncluded((current) => event.target.checked ? [...current, candidate.id] : current.filter((id) => id !== candidate.id))} className="mt-1 size-4 accent-primary" /><span><span className="font-medium">Short {index + 1} · {candidate.hook || candidate.stable_key}</span><span className="mt-1 block text-xs text-muted-foreground">{candidate.languages.map((item) => `${item.language.toUpperCase()} · ${item.render_status}`).join("  /  ") || "No language render"}</span></span></label>
            <div className="mt-3 flex flex-wrap gap-2">{candidate.languages.map((item) => <span key={item.language} className="rounded bg-muted px-2 py-1 text-[11px]">{item.language.toUpperCase()} · {item.approval_state}</span>)}</div>
          </div>
          {reference?.video_url ? <video controls className="aspect-[9/16] max-h-64 w-full rounded bg-black object-contain" src={apiFileUrl(reference.video_url)} aria-label={`Short ${index + 1} preview`} /> : <div className="flex aspect-[9/16] max-h-64 items-center justify-center rounded border border-dashed text-xs text-muted-foreground">Preview not ready</div>}
        </article>;
      })}
    </section>
    <section className="space-y-3"><div><h2 className="text-lg font-semibold">Distribution calendar</h2><p className="text-sm text-muted-foreground">{slots.length} planned platform-language placements · local time {doc.publish_time_local}</p></div><div className="overflow-x-auto rounded-lg border border-border"><table className="w-full min-w-[650px] text-left text-sm"><thead className="bg-muted/50"><tr><th className="px-3 py-2">Day</th><th className="px-3 py-2">Date</th><th className="px-3 py-2">Short</th><th className="px-3 py-2">Language</th><th className="px-3 py-2">Platform</th><th className="px-3 py-2">Destination</th></tr></thead><tbody>{slots.map((slot) => <tr key={slot.slot_key} className="border-t border-border"><td className="px-3 py-2">{slot.day}</td><td className="px-3 py-2">{slotDate(startDate || doc.start_date, slot.day)}</td><td className="px-3 py-2">Short {slot.short_number}</td><td className="px-3 py-2">{slot.language.toUpperCase()}</td><td className="px-3 py-2">{slot.platform_label}</td><td className="px-3 py-2 text-xs text-muted-foreground">{slot.channel_url || (slot.status === "configured" ? "Credential reference set" : slot.status === "render_not_ready" ? "Render not ready" : "Account not connected")}</td></tr>)}</tbody></table></div></section>
    {message && <p role="status" className="rounded border border-border px-3 py-2 text-sm">{message}</p>}
    <div className="flex flex-wrap justify-end gap-2"><Button variant="outline" disabled={busy || !startDate} loading={busy} onClick={() => void save(false)}><Save /> Save draft</Button><Button disabled={busy || !startDate || included.length === 0} loading={busy} onClick={() => void save(true)}><Check /> Approve release plan</Button></div>
  </main>;
}
