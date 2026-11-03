"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { PhoneticSample } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { InlineAlert } from "../shared";

const STATUS_LABEL: Record<string, string> = {
  disputed: "Disputed",
  agent_consensus: "Agent consensus (not verified)",
  unlabeled: "No opinion yet",
  human_verified: "Verified by ear",
  unclear: "Unclear",
};
const STATUS_TONE: Record<string, "warning" | "success" | "danger" | "info" | "default"> = {
  disputed: "danger",
  agent_consensus: "info",
  unlabeled: "default",
  human_verified: "success",
  unclear: "warning",
};
const CATEGORIES = ["short_vowel", "ezafe", "colloquial", "name", "connected", "other"];
const KIND_LABEL: Record<string, string> = { agent: "Agent", recognizer: "Recognizer", human: "Person" };

function Waveform({ peaks, progress }: { peaks: number[]; progress: number }) {
  return (
    <div className="flex h-12 items-center gap-px" dir="ltr" aria-hidden="true">
      {peaks.map((p, i) => (
        <span key={i} className={i / peaks.length <= progress ? "bg-primary" : "bg-muted-foreground/40"} style={{ height: `${Math.max(4, p * 100)}%`, width: 3 }} />
      ))}
    </div>
  );
}

/** One clip: playback with waveform, every opinion with its kind, and the person's label (the only ground truth). */
function Clip({ sample, reviewer, onChanged }: { sample: PhoneticSample; reviewer: string; onChanged: () => void }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [progress, setProgress] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const peaks = useApi(() => api.phoneticPeaks(sample.id), [sample.id]);
  const named = reviewer.trim().length > 0;

  async function label(choice: string) {
    setBusy(choice);
    setError(null);
    try {
      await api.labelPhoneticSample(sample.id, choice, reviewer);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The label was not saved");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span lang="fa" dir="rtl" className="text-xl">{sample.surface}</span>
        <Badge variant={STATUS_TONE[sample.status]}>{STATUS_LABEL[sample.status]}</Badge>
        <span className="text-xs text-muted-foreground">{sample.category} · {sample.source}{sample.speaker ? ` · ${sample.speaker}` : ""} · {sample.speed}</span>
      </div>
      {sample.sentence && <p lang="fa" dir="rtl" className="text-sm text-muted-foreground">{sample.sentence}</p>}
      <audio
        ref={audio}
        controls
        src={api.phoneticAudioUrl(sample.id)}
        className="w-full"
        onTimeUpdate={(e) => setProgress(e.currentTarget.duration ? e.currentTarget.currentTime / e.currentTarget.duration : 0)}
      />
      {peaks.data && <Waveform peaks={peaks.data.peaks} progress={progress} />}
      <div className="space-y-1">
        <p className="text-sm font-medium">What did you hear?</p>
        {!named && <p className="text-xs text-muted-foreground">Enter your name above: a label is a person&apos;s statement.</p>}
        <div className="flex flex-wrap gap-2">
          {sample.candidates.map((c) => (
            <Button key={c.key} variant={sample.human_key === c.key ? "default" : "secondary"} disabled={!named} loading={busy === c.key} onClick={() => label(c.key)}>
              <span lang="fa" dir="rtl" className="text-base">{c.irab}</span>
              <span className="ms-2 text-xs opacity-70">{c.key} · /{c.ipa}/</span>
            </Button>
          ))}
          <Button variant="secondary" disabled={!named} loading={busy === "unclear"} onClick={() => label("unclear")}>Cannot tell</Button>
        </div>
        {error && <InlineAlert tone="error">{error}</InlineAlert>}
      </div>
      <table className="w-full text-xs">
        <caption className="pb-1 text-start font-medium">Opinions (agents and recognizers are not ground truth)</caption>
        <tbody>
          {sample.opinions.length === 0 && <tr><td className="text-muted-foreground">None yet.</td></tr>}
          {sample.opinions.map((o) => (
            <tr key={o.actor} className="border-t">
              <td className="py-1">{KIND_LABEL[o.kind]}</td>
              <td>{o.actor}</td>
              <td>{o.choice}</td>
              <td className="text-end">{o.confidence ? o.confidence.toFixed(2) : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Record in the page or upload a file as a new clip. */
function Recorder({ language, speaker, onAdded }: { language: string; speaker: string; onAdded: () => void }) {
  const [surface, setSurface] = useState("");
  const [candidates, setCandidates] = useState("");
  const [category, setCategory] = useState("short_vowel");
  const [speed, setSpeed] = useState("normal");
  const [blob, setBlob] = useState<{ data: Blob; name: string } | null>(null);
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const preview = useMemo(() => (blob ? URL.createObjectURL(blob.data) : null), [blob]);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  async function start() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks: Blob[] = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        setBlob({ data: new Blob(chunks, { type: rec.mimeType }), name: "recording.webm" });
      };
      rec.start();
      recorder.current = rec;
      setRecording(true);
    } catch {
      setError("The microphone is not available (permission denied or no device).");
    }
  }

  function stop() {
    recorder.current?.stop();
    setRecording(false);
  }

  async function save() {
    if (!blob) return;
    setBusy(true);
    setError(null);
    try {
      await api.uploadPhoneticSample({ file: blob.data, filename: blob.name, surface, sentence: "", candidates, category, speaker, speed, language });
      setBlob(null);
      setSurface("");
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : "The clip was not saved");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-md border p-3">
      <p className="text-sm font-medium">Add a clip · record or upload</p>
      <div className="flex flex-wrap gap-2">
        <Input className="w-32" lang="fa" dir="rtl" aria-label="Word" placeholder="word" value={surface} onChange={(e) => setSurface(e.target.value)} />
        <Input className="w-64" aria-label="Candidate readings" placeholder="readings, comma-separated: ahd, ohd" value={candidates} onChange={(e) => setCandidates(e.target.value)} />
        <select className="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Category" value={category} onChange={(e) => setCategory(e.target.value)}>
          {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
        </select>
        <select className="h-9 rounded-md border bg-background px-2 text-sm" aria-label="Speed" value={speed} onChange={(e) => setSpeed(e.target.value)}>
          {["slow", "normal", "fast"].map((c) => <option key={c}>{c}</option>)}
        </select>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {recording ? <Button onClick={stop}>Stop</Button> : <Button variant="secondary" onClick={start}>Record</Button>}
        <label className="text-xs text-muted-foreground">
          or upload{" "}
          <input type="file" accept="audio/*" aria-label="Upload audio" onChange={(e) => { const f = e.target.files?.[0]; if (f) setBlob({ data: f, name: f.name }); }} />
        </label>
        {preview && <audio controls src={preview} />}
        <Button disabled={!blob || !surface.trim() || candidates.split(",").filter((c) => c.trim()).length < 2} loading={busy} onClick={save}>Save clip</Button>
      </div>
      {error && <InlineAlert tone="error">{error}</InlineAlert>}
    </div>
  );
}

/** The phonetic benchmark: the clips (disputed first), the report, recording and labelling. */
export function PhoneticBenchmark({ language, reviewer }: { language: string; reviewer: string }) {
  const [status, setStatus] = useState<string>("");
  const [category, setCategory] = useState<string>("");
  const [selected, setSelected] = useState<number | null>(null);
  const state = useApi(() => api.phoneticSamples(language, status || undefined, category || undefined), [language, status, category]);
  const samples = useMemo(() => state.data?.samples ?? [], [state.data]);
  const current = samples.find((s) => s.id === selected) ?? samples[0] ?? null;
  const report = state.data?.report;

  return (
    <div className="space-y-3">
      <p className="text-xs leading-5 text-muted-foreground">
        Clips are cut from the existing narration or recorded here. <strong>Agent consensus is not ground truth</strong>: only a label from a person who
        listened is. A recognizer is judged against those labels only; no recognizer is chosen and nothing is approved automatically.
      </p>
      {report && (
        <div className="flex flex-wrap gap-2 text-xs" aria-label="Benchmark report">
          <Badge>{report.total} clips</Badge>
          <Badge variant="success">{report.human_verified} verified by ear</Badge>
          {Object.entries(report.counts).map(([k, v]) => <Badge key={k} variant={STATUS_TONE[k]}>{STATUS_LABEL[k]}: {v}</Badge>)}
          <Badge variant={report.enough_for_a_verdict ? "success" : "warning"}>
            {report.enough_for_a_verdict ? "Enough labels for a verdict" : "Not enough labels for a verdict (need 60)"}
          </Badge>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {["", "disputed", "agent_consensus", "unlabeled", "human_verified", "unclear"].map((s) => (
          <Button key={s || "all"} size="sm" variant={status === s ? "default" : "secondary"} onClick={() => { setStatus(s); setSelected(null); }}>{s ? STATUS_LABEL[s] : "All"}</Button>
        ))}
        <select className="h-8 rounded-md border bg-background px-2 text-xs" aria-label="Category filter" value={category} onChange={(e) => { setCategory(e.target.value); setSelected(null); }}>
          <option value="">All categories</option>
          {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
        </select>
      </div>
      {state.error && <ErrorState message={state.error} onRetry={state.refetch} />}
      {!state.data && !state.error && <Skeleton className="h-48 w-full" />}
      {state.data && samples.length === 0 && <EmptyState title="No clips here" description="No clip has this status or category." />}
      {current && (
        <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
          <ul className="max-h-[32rem] space-y-1 overflow-y-auto" aria-label="Clips">
            {samples.map((s) => (
              <li key={s.id}>
                <button type="button" onClick={() => setSelected(s.id)} className={`flex w-full items-center justify-between gap-2 rounded-md border px-3 py-1.5 text-start text-sm ${s.id === current.id ? "border-primary bg-primary/5" : "border-border"}`}>
                  <span lang="fa" dir="rtl" className="text-base">{s.surface}</span>
                  <Badge variant={STATUS_TONE[s.status]}>{STATUS_LABEL[s.status]}</Badge>
                </button>
              </li>
            ))}
          </ul>
          <Clip key={current.id} sample={current} reviewer={reviewer} onChanged={state.refetch} />
        </div>
      )}
      <Recorder language={language} speaker={reviewer} onAdded={state.refetch} />
    </div>
  );
}
