"use client";

import { useMemo, useRef, useState } from "react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { isRtl } from "@/lib/format";
import type { PronunciationPlanWord } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";

const LEVEL_TONE = { LOW: "default", MEDIUM: "info", HIGH: "warning", CRITICAL: "danger" } as const;
const FILTERS = [
  { key: "changed", label: "Changed for the voice", keep: (w: PronunciationPlanWord) => !["keep", "escalate"].includes(w.decision.strategy) },
  { key: "escalated", label: "Left for you", keep: (w: PronunciationPlanWord) => w.decision.strategy === "escalate" },
  { key: "risky", label: "High risk, kept", keep: (w: PronunciationPlanWord) => w.decision.strategy === "keep" },
  { key: "all", label: "All", keep: () => true },
] as const;

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[10rem_1fr] gap-3 border-b border-border/60 py-1.5 text-sm last:border-b-0">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="min-w-0">{children}</div>
    </div>
  );
}

/** Why one word is or is not changed for the voice: its reference, ezafe, risk, decision, the exact voice input, trust and history. */
function Detail({ w, lang, audioUrl }: { w: PronunciationPlanWord; lang: string; audioUrl: string | null }) {
  const audio = useRef<HTMLAudioElement>(null);
  const ref = w.reference;
  const words = w.sentence.split(" ");
  const rtl = isRtl(lang);
  const play = () => {
    const el = audio.current;
    if (!el || !w.audio) return;
    el.currentTime = w.audio.start;
    void el.play();
    const stop = () => { if (el.currentTime >= w.audio!.end) { el.pause(); el.removeEventListener("timeupdate", stop); } };
    el.addEventListener("timeupdate", stop);
  };
  return (
    <div className="space-y-3" aria-label="Word decision">
      <div className="flex flex-wrap items-center gap-2">
        <span lang={lang} dir={rtl ? "rtl" : "ltr"} className="text-2xl">{w.word}</span>
        <Badge variant={LEVEL_TONE[w.risk.level]}>{w.risk.level} · {w.risk.score}</Badge>
        <Badge variant={w.decision.strategy === "escalate" ? "warning" : w.decision.strategy === "keep" ? "default" : "accent"}>{w.decision.strategy}</Badge>
        <Badge variant={w.verification === "UNVERIFIED" ? "outline" : "success"}>{w.verification}</Badge>
      </div>
      <p lang={lang} dir={rtl ? "rtl" : "ltr"} className="rounded-md bg-muted/40 p-2 text-base leading-8">
        {words.map((t, i) => (
          <span key={i} className={i === w.n ? "rounded bg-primary/20 px-1 font-semibold" : undefined}>{t}{" "}</span>
        ))}
      </p>
      <div>
        <Row label="Expected (Finglish)"><span dir="ltr">{w.finglish}</span></Row>
        <Row label="Phonemes (IPA)"><span dir="ltr">/{ref?.ipa ?? "—"}/</span></Row>
        <Row label="Syllables">
          <span dir="ltr">{ref?.syllables.map((s, i) => <span key={i} className={i === ref.stress ? "font-semibold underline" : undefined}>{i ? "·" : ""}{s}</span>) ?? "—"}</span>
          <span className="ml-2 text-xs text-muted-foreground">stress by rule, not measured</span>
        </Row>
        <Row label="Morphology"><span lang={lang}>{ref?.morphology.map((m) => `${m.text} (${m.kind})`).join(" + ") ?? "—"}</span></Row>
        <Row label="REFERENCE_IRAB">
          <span lang={lang} dir={rtl ? "rtl" : "ltr"} className="text-xl">{ref?.reference_irab ?? "—"}</span>
          {ref && (ref.valid ? <Badge variant="success" className="ml-2">validator: valid</Badge>
            : <span className="ml-2 text-xs text-rose-600 dark:text-rose-400">validator: {ref.problems.join("; ")}</span>)}
        </Row>
        <Row label="Ezafe">
          {w.ezafe ? <span dir="ltr">{w.ezafe.ezafe_type} · {w.ezafe.phonological_class} · {w.ezafe.grammatical_relation} · {w.ezafe.evidence_type}
            {w.ezafe.written ? " · written in the text" : ""} · expected <em>{w.ezafe.expected_reading}</em></span> : "none after this word"}
        </Row>
        <Row label="Why this risk">
          <ul className="space-y-0.5">
            {w.risk.signals.filter((s) => s.weight !== 0).map((s) => <li key={s.name} className="text-xs"><span className="font-medium">{s.name}</span> (+{s.weight}): {s.evidence}</li>)}
            {w.risk.signals.length === 0 && <li className="text-xs text-muted-foreground">no signal</li>}
          </ul>
        </Row>
        <Row label="Decision"><span className="text-xs">{w.decision.basis}</span></Row>
        <Row label="ElevenLabs input">
          <span lang={lang} dir={rtl ? "rtl" : "ltr"} className="text-xl">{w.tts_input}</span>
          <span className="ml-2 text-xs text-muted-foreground">{w.tts_input === w.word ? "unchanged" : `original: ${w.word}`}</span>
        </Row>
        <Row label="Correction history">
          {w.memory.length === 0 ? <span className="text-xs text-muted-foreground">nothing in the memory</span> : (
            <ul className="space-y-0.5 text-xs">
              {w.memory.map((m) => (
                <li key={m.id}>
                  <span lang={lang}>{m.surface}</span> · {m.trust}{m.next_word ? ` · before ${m.next_word}` : ""}
                  {m.interventions.map((i, k) => <span key={k}> · {i.kind} <span lang={lang}>{i.replacement}</span> {i.status} ({i.successes}✓ {i.failures}✗)</span>)}
                </li>
              ))}
            </ul>
          )}
        </Row>
      </div>
      {audioUrl && w.audio ? (
        <div className="flex items-center gap-2">
          <Button size="sm" variant="secondary" onClick={play}>Play the sentence</Button>
          <span className="text-xs text-muted-foreground">from the film&apos;s current narration (made before this plan: it may not have this change)</span>
          <audio ref={audio} preload="none" src={apiFileUrl(audioUrl)} aria-label="Narration" />
        </div>
      ) : <p className="text-xs text-muted-foreground">No narration of this version to play.</p>}
    </div>
  );
}

/** Why each word of the narration is or is not changed for the voice (the planner run dry: no model, no voice, nothing written). */
export function PronunciationPlanView({ versionId, language }: { versionId: number | null; language: string }) {
  const plan = useApi(() => (versionId == null ? Promise.resolve(null) : api.pronunciationPlan(versionId, language)), [versionId, language]);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("changed");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const words = useMemo(() => {
    const keep = FILTERS.find((f) => f.key === filter)!.keep;
    return (plan.data?.words ?? []).filter((w) => keep(w) && (!query.trim() || w.word.includes(query.trim()) || w.finglish.includes(query.trim())));
  }, [plan.data, filter, query]);
  if (versionId == null) return <EmptyState title="No version" description="This language has no story version yet." />;
  if (plan.error) return <ErrorState message={plan.error} onRetry={plan.refetch} />;
  if (!plan.data) return <Skeleton className="h-64 w-full" />;
  const c = plan.data.counts;
  const key = (w: PronunciationPlanWord) => `${w.block_id}:${w.n}`;
  const current = words.find((w) => key(w) === picked) ?? words[0];
  return (
    <div className="space-y-3">
      <p className="text-xs leading-5 text-muted-foreground">
        {c.words.toLocaleString()} words · LOW {c.LOW} · MEDIUM {c.MEDIUM} · HIGH {c.HIGH} · CRITICAL {c.CRITICAL} · {c.changed} changed for the voice ·
        {" "}{c.escalated} left for you · reference valid for {c.reference_valid} of {c.reference_checked}. Linguistic decisions only: nothing here is verified by
        listening (every word stays UNVERIFIED until a person or a validated recognizer confirms it).
      </p>
      {plan.data.consistency.some((v) => !v.legitimate) && (
        <details className="rounded-md border border-border p-2 text-sm" aria-label="Read differently across blocks">
          <summary className="cursor-pointer text-xs font-medium">
            Read differently across blocks ({plan.data.consistency.filter((v) => !v.legitimate).length} words; names: {plan.data.consistency.filter((v) => v.kind === "name" && !v.legitimate).length})
          </summary>
          <ul className="mt-2 max-h-48 space-y-0.5 overflow-y-auto text-xs">
            {plan.data.consistency.filter((v) => !v.legitimate).map((v) => (
              <li key={v.word}>
                <span lang={language} className="text-sm">{v.word}</span> {v.kind === "name" ? "(name) " : ""}·{" "}
                <span dir="ltr">{Object.entries(v.readings).map(([r, b]) => `${r} ×${b.length}`).join(" / ")}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Words">
        {FILTERS.map((f) => (
          <Button key={f.key} size="sm" variant={filter === f.key ? "default" : "secondary"} onClick={() => { setFilter(f.key); setPicked(null); }}>
            {f.label} <span className="opacity-70">({(plan.data?.words ?? []).filter(f.keep).length})</span>
          </Button>
        ))}
        <Input className="h-8 w-48" placeholder="Find a word" aria-label="Find a word" value={query} onChange={(e) => setQuery(e.target.value)} />
      </div>
      {words.length === 0 || !current ? <EmptyState title="No word here" description="No word matches this filter." /> : (
        <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
          <ul className="max-h-[36rem] space-y-1 overflow-y-auto" aria-label="Words of the plan">
            {words.map((w) => (
              <li key={key(w)}>
                <button type="button" onClick={() => setPicked(key(w))}
                  className={`flex w-full items-center justify-between gap-2 rounded-md border px-3 py-1.5 text-start text-sm ${key(w) === key(current) ? "border-primary bg-primary/5" : "border-border"}`}>
                  <span lang={language} dir={isRtl(language) ? "rtl" : "ltr"} className="text-base">{w.word}</span>
                  <span className="text-xs text-muted-foreground" dir="ltr">{w.block_id.replace(/^[A-Z]+_/, "")}</span>
                </button>
              </li>
            ))}
          </ul>
          <Detail key={key(current)} w={current} lang={language} audioUrl={plan.data.audio_url} />
        </div>
      )}
    </div>
  );
}
