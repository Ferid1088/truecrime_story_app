"use client";

import { useEffect, useState } from "react";
import { metaPublishingApi, type MetaReport, type MetaStep } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

const OPEN = new Set(["created", "uploading", "processing", "publishing"]);

function Row({ label, value, tone }: { label: string; value: React.ReactNode; tone?: "ok" | "bad" }) {
  const color = tone === "ok" ? "text-emerald-700" : tone === "bad" ? "text-rose-700" : "";
  return <div className="flex justify-between gap-4 border-b py-2 text-sm"><span className="text-muted-foreground">{label}</span><span className={`text-right font-medium ${color}`}>{value}</span></div>;
}

export default function MetaPublishingPage() {
  const { data, loading, error, refetch } = useApi(() => metaPublishingApi.status());
  const [clipKey, setClipKey] = useState("C1");
  const [report, setReport] = useState<MetaReport | null>(null);
  const [auth, setAuth] = useState<MetaStep[] | null>(null);
  const [message, setMessage] = useState("");
  const [confirm, setConfirm] = useState("");
  const busy = data?.operations.some((o) => OPEN.has(o.status)) ?? false;

  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(refetch, 5000);
    return () => clearInterval(timer);
  }, [busy, refetch]);

  if (loading && !data) return <Skeleton className="h-[480px]" />;
  if (error && !data) return <p className="p-8 text-sm text-rose-700">{error}</p>;
  if (!data) return null;
  const clip = data.clips.find((c) => c.clip_key === clipKey);
  const operation = data.operations.find((o) => o.clip_key === clipKey);
  const required = data.confirmation_template.replace("<clip>", clipKey);
  const guard = async (work: () => Promise<unknown>) => {
    setMessage("");
    try { await work(); } catch (e) { setMessage(e instanceof Error ? e.message : String(e)); }
    refetch();
  };

  return (
    <main className="mx-auto max-w-4xl space-y-8 px-6 py-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Instagram Reels test publisher</h1>
        <p className="mt-1 text-sm text-muted-foreground">Farsi only. Nothing here is automatic: every upload and every publish is started by you.</p>
      </div>

      <section>
        <Row label="Case / language" value={`Case ${data.case_id} / ${data.language}`} />
        <Row label="Destination" value={`@${data.account.username} (${data.account.brand}) · ${data.account.ig_account_id}`} />
        <Row label="Default mode" value={data.default_mode} />
        <Row label="Live calls" value={data.live_enabled ? "enabled" : "off (config/meta_publishing.json)"} tone={data.live_enabled ? "ok" : "bad"} />
        <Row label="API authorization" value={auth ? auth.map((s) => s.detail).join(" · ") : data.token.configured ? `token set (${data.token.source}), not checked yet` : "no token configured"} tone={auth ? "ok" : data.token.configured ? undefined : "bad"} />
      </section>

      <section className="space-y-3">
        <label className="block text-sm font-medium">Clip
          <select aria-label="Clip" value={clipKey} onChange={(e) => { setClipKey(e.target.value); setReport(null); }} className="mt-2 block rounded border bg-background px-3 py-2 font-normal">
            {data.clips.map((c) => <option key={c.clip_key} value={c.clip_key}>{c.clip_key} · {c.file} · {c.seconds} s · {c.verdict}</option>)}
          </select>
        </label>
        {clip && <Row label="Video validation (Instagram preflight)" value={clip.error ?? clip.verdict} tone={clip.verdict === "PASS" ? "ok" : clip.verdict === "FAIL" || clip.error ? "bad" : undefined} />}
        {clip?.failing.map((f) => <p key={f.name} className="text-sm text-muted-foreground">{f.name}: {f.actual} (needs {f.expected}). {f.action}</p>)}
      </section>

      <section className="flex flex-wrap gap-3">
        <Button type="button" variant="outline" onClick={() => void guard(async () => setReport(await metaPublishingApi.dryRun(clipKey)))}>Dry run (no request)</Button>
        <Button type="button" variant="outline" disabled={!data.live_enabled} onClick={() => void guard(async () => setAuth((await metaPublishingApi.authorization()).steps))}>Check authorization</Button>
        <Button type="button" disabled={!data.live_enabled || clip?.verdict === "FAIL" || busy} onClick={() => void guard(() => metaPublishingApi.start(clipKey, "UPLOAD_ONLY"))}>Upload only (no publish)</Button>
      </section>

      <section className="space-y-3 border-t pt-5">
        <div className="text-sm font-medium">Publish (public)</div>
        <p className="text-sm text-muted-foreground">Type exactly <code>{required}</code> to allow publishing the prepared Reel.</p>
        <input aria-label="Publish confirmation" value={confirm} onChange={(e) => setConfirm(e.target.value)} className="block w-full rounded border bg-background px-3 py-2 text-sm" />
        <Button type="button" variant="destructive" disabled={confirm !== required || operation?.status !== "ready"} onClick={() => void guard(async () => { await metaPublishingApi.start(clipKey, "PUBLISH", confirm); setConfirm(""); })}>Publish now</Button>
      </section>

      {message && <p role="alert" className="text-sm text-rose-700">{message}</p>}

      <section>
        <Row label="Upload status" value={operation ? operation.status : "no operation yet"} />
        <Row label="Container" value={operation?.container_id ?? "-"} />
        <Row label="Container processing" value={operation ? (operation.status === "ready" || operation.status === "published" ? "FINISHED" : OPEN.has(operation.status) ? "in progress" : operation.status) : "-"} />
        <Row label="Publish status" value={operation ? (operation.status === "published" ? "published" : operation.status === "publish_uncertain" ? "unknown: check Instagram" : "not published") : "-"} />
        <Row label="Published Reel" value={operation?.permalink ? <a className="underline" href={operation.permalink} target="_blank" rel="noreferrer">{operation.permalink}</a> : "-"} />
        {operation?.error && <p className="mt-2 text-sm text-rose-700">{operation.error}</p>}
      </section>

      {report && (
        <section className="space-y-2 border-t pt-5 text-sm">
          <div className="font-medium">Dry run: {report.clip_key}</div>
          {report.steps.map((s) => <div key={s.name}><span className="font-medium">{s.name}</span> <span className="text-muted-foreground">[{s.status}] {s.detail}</span></div>)}
          {report.manual_steps.map((m) => <p key={m.action} className="text-muted-foreground">Manual: {m.where}. {m.reason}</p>)}
        </section>
      )}
    </main>
  );
}
