"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Check, Copy, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { StoryFull, StoryMeta } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/page-header";
import { parseNarrativePlan } from "@/lib/format";
import { EmptyState, ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { StoryReader } from "@/components/story/reader";
import { QualityPanel } from "@/components/story/quality-panel";
import { VersionList } from "@/components/story/version-list";
import { Localizations } from "@/components/story/localizations";
import { Badge } from "@/components/ui/badge";

const TONES = [
  { value: "investigative", label: "Investigative" },
  { value: "cinematic, suspenseful, investigative, respectful", label: "Cinematic" },
  { value: "psychological, introspective, character-driven", label: "Psychological" },
  { value: "documentary, factual, measured", label: "Documentary" },
  { value: "dark, atmospheric, slow-burn", label: "Dark" },
  { value: "minimal, terse, factual", label: "Minimal" },
  { value: "__custom__", label: "Custom…" },
];

const DURATIONS = [20, 30, 40, 45, 60];

const IMPROVE_ACTIONS = [
  { label: "Improve Hook", instruction: "Rewrite the opening hook so it creates an immediate, strong curiosity gap without spoiling later reveals." },
  { label: "Improve Pacing", instruction: "Improve pacing: tighten slow stretches, balance scene length, and keep forward momentum throughout." },
  { label: "More Suspense", instruction: "Increase suspense: strengthen open loops and tension between reveals, without inventing facts." },
  { label: "Reduce Repetition", instruction: "Remove redundancy: merge repeated claims, cut restated exposition, keep each fact stated once." },
  { label: "Improve Ending", instruction: "Improve the ending so it lands with emotional weight and clear resolution of the central mystery." },
];

export function Studio() {
  const params = useSearchParams();
  const initialCase = params.get("case");

  const { data: cases, error: casesError, loading: casesLoading } = useApi(() => api.listCases());
  const [caseId, setCaseId] = useState<number | null>(initialCase ? Number(initialCase) : null);

  if (casesLoading) return <Skeleton className="h-[70vh]" />;
  if (casesError) return <ErrorState message={casesError} />;
  if (!cases || cases.length === 0)
    return (
      <EmptyState
        title="No cases to write about"
        description="Create or discover a case first, add sources, and run research."
      />
    );

  const effectiveId = caseId ?? cases[0].id;
  return <StudioBody caseId={effectiveId} cases={cases} onCaseChange={setCaseId} />;
}

function StudioBody({
  caseId,
  cases,
  onCaseChange,
}: {
  caseId: number;
  cases: { id: number; title: string }[];
  onCaseChange: (id: number) => void;
}) {
  const [language, setLanguage] = useState("fa");
  const [minutes, setMinutes] = useState(45);
  const [tone, setTone] = useState(TONES[1].value);
  const [customTone, setCustomTone] = useState("");
  const [stories, setStories] = useState<Record<number, StoryFull>>({});
  const [busy, setBusy] = useState<"generate" | "improve" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [customInstruction, setCustomInstruction] = useState("");
  const [showCustom, setShowCustom] = useState(false);

  const story = stories[caseId] ?? null;
  const setStory = (s: StoryFull) => setStories((prev) => ({ ...prev, [caseId]: s }));

  const { data: caseDetail, refetch: refetchCase } = useApi(() => api.getCase(caseId), [caseId]);
  const { data: versions, refetch: refetchVersions } = useApi(() => api.listStories(caseId), [caseId]);
  const [masterState, setMasterState] = useState<{
    caseId: number;
    master: StoryFull | null;
  }>({ caseId, master: null });

  useEffect(() => {
    api
      .getMasterStory(caseId)
      .then((res) => setMasterState({ caseId, master: res.master }))
      .catch(() => setMasterState({ caseId, master: null }));
  }, [caseId, versions]);

  const master = masterState.caseId === caseId ? masterState.master : null;

  useEffect(() => {
    const latest = versions?.[0];
    if (latest && !stories[caseId]) {
      api
        .getStoryVersion(caseId, latest.id)
        .then((s) => setStories((prev) => ({ ...prev, [caseId]: s })))
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [versions]);

  const effectiveTone = tone === "__custom__" ? customTone || "cinematic" : tone;

  async function generate() {
    setBusy("generate");
    setError(null);
    try {
      const res = await api.generateStory(caseId, {
        target_minutes: minutes,
        language,
        tone: effectiveTone,
      });
      const full = await api.getStoryVersion(caseId, res.story_version_id);
      setStory(full);
      refetchVersions();
      refetchCase();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function improve(instruction: string) {
    if (!story) return;
    setBusy("improve");
    setError(null);
    try {
      const v = await api.improveStory(caseId, story.id, instruction);
      setStory(v);
      setCustomInstruction("");
      setShowCustom(false);
      refetchVersions();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function copyStory() {
    if (!story) return;
    await navigator.clipboard.writeText(story.story_text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  const narrativeAngle = story?.narrative_angle ?? caseDetail?.narrative_angle;

  async function generateMaster() {
    setBusy("generate");
    setError(null);
    try {
      const m = await api.generateMasterStory(caseId, {
        target_minutes: minutes,
        language: "en",
        tone: effectiveTone,
      });
      setMasterState({ caseId, master: m });
      setStory(m);
      refetchVersions();
      refetchCase();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  function openVersion(v: StoryMeta) {
    if (v.kind === "localized") {
      api.getLocalization(v.id).then(setStory).catch(() => {});
    } else {
      api.getStoryVersion(caseId, v.id).then(setStory).catch(() => {});
    }
  }

  return (
    <div>
      <PageHeader title="Story Studio" description="Direct, write, and refine long-form stories." />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[280px_1fr]">
        {/* Left: controls */}
        <div className="space-y-4">
          <Card>
            <CardContent className="space-y-3 p-4">
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Case</label>
                <Select
                  value={caseId}
                  onChange={(e) => {
                    onCaseChange(Number(e.target.value));
                    setError(null);
                  }}
                >
                  {cases.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.title}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Language</label>
                <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
                  <option value="fa">Persian</option>
                  <option value="en">English</option>
                  <option value="de">German</option>
                  <option value="ar">Arabic</option>
                </Select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Target duration</label>
                <Select value={minutes} onChange={(e) => setMinutes(Number(e.target.value))}>
                  {DURATIONS.map((m) => (
                    <option key={m} value={m}>
                      {m} min
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <label className="mb-1 block text-xs font-medium text-muted-foreground">Tone</label>
                <Select value={tone} onChange={(e) => setTone(e.target.value)}>
                  {TONES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </Select>
                {tone === "__custom__" && (
                  <Input
                    className="mt-2"
                    placeholder="e.g. cold, forensic, restrained"
                    value={customTone}
                    onChange={(e) => setCustomTone(e.target.value)}
                  />
                )}
              </div>
              <Button className="w-full" onClick={generate} loading={busy === "generate"} disabled={busy !== null}>
                <Sparkles className="size-4" /> Generate Story
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                Master Story
                {master && (
                  <Badge
                    variant={master.status === "ready" ? "default" : "outline"}
                  >
                    {master.status === "ready"
                      ? "ready for human review"
                      : master.status.replace("_", " ")}
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 p-3">
              {master ? (
                <>
                  <div className="grid grid-cols-2 gap-1 text-[11px] text-muted-foreground">
                    <span>
                      v{master.version} · English
                    </span>
                    <span>
                      {master.word_count.toLocaleString()} words
                    </span>
                    <span>
                      Eng <b className="text-foreground">{Math.round(master.engagement_score)}</b>
                    </span>
                    <span>
                      ~{Math.max(1, Math.round(master.word_count / 150))} min
                    </span>
                  </div>
                  <Button
                    size="sm"
                    variant="secondary"
                    className="w-full"
                    disabled={busy !== null}
                    onClick={() => openVersion(master)}
                  >
                    Open Master v{master.version}
                  </Button>
                </>
              ) : (
                <p className="text-[11px] leading-4 text-muted-foreground">
                  No English master yet. The master is the canonical narrative
                  every localization derives from.
                </p>
              )}
              <Button
                size="sm"
                className="w-full"
                disabled={busy !== null}
                loading={busy === "generate"}
                onClick={generateMaster}
              >
                {master ? "New Master Version" : "Generate English Master"}
              </Button>
            </CardContent>
          </Card>

          <Localizations
            caseId={caseId}
            masterReady={master?.status === "ready"}
            onOpen={openVersion}
          />

          {narrativeAngle && (
            <Card>
              <CardHeader>
                <CardTitle>Narrative Angle</CardTitle>
              </CardHeader>
              <CardContent>
                {(() => {
                  const plan = parseNarrativePlan(narrativeAngle);
                  if (plan) {
                    return (
                      <div className="space-y-2">
                        {plan.title && (
                          <p className="text-xs font-medium leading-5 text-foreground" dir="auto">
                            {plan.title}
                          </p>
                        )}
                        {plan.central_question && (
                          <p className="text-xs leading-5 text-foreground/80" dir="auto">
                            {plan.central_question}
                          </p>
                        )}
                      </div>
                    );
                  }
                  return (
                    <p className="whitespace-pre-wrap text-xs leading-5 text-foreground/80" dir="auto">
                      {narrativeAngle}
                    </p>
                  );
                })()}
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle>Refine</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 p-3">
              {IMPROVE_ACTIONS.map((a) => (
                <Button
                  key={a.label}
                  variant="secondary"
                  size="sm"
                  className="w-full justify-start"
                  disabled={!story || busy !== null}
                  onClick={() => improve(a.instruction)}
                >
                  {a.label}
                </Button>
              ))}
              <Button
                variant="secondary"
                size="sm"
                className="w-full justify-start"
                disabled={!story || busy !== null}
                onClick={() => setShowCustom((v) => !v)}
              >
                Rewrite Section…
              </Button>
              {showCustom && (
                <div className="space-y-2 pt-1">
                  <Textarea
                    autoFocus
                    rows={3}
                    placeholder="e.g. Rewrite the section about the trial to be tighter and more chronological."
                    value={customInstruction}
                    onChange={(e) => setCustomInstruction(e.target.value)}
                  />
                  <Button
                    size="sm"
                    className="w-full"
                    disabled={!story || customInstruction.trim().length < 3 || busy !== null}
                    loading={busy === "improve"}
                    onClick={() => improve(customInstruction.trim())}
                  >
                    Improve Story
                  </Button>
                </div>
              )}
              <p className="pt-1 text-[11px] leading-4 text-muted-foreground">
                Each refinement creates a new version — previous versions are preserved.
              </p>
            </CardContent>
          </Card>

          {versions && versions.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">Versions</p>
              <VersionList
                versions={versions}
                selectedId={story?.id ?? null}
                onSelect={(v) => api.getStoryVersion(caseId, v.id).then(setStory)}
              />
            </div>
          )}
        </div>

        {/* Right: story + quality */}
        <div className="grid grid-cols-1 min-w-0 gap-4 xl:grid-cols-[1fr_260px]">
          <Card className="min-w-0">
            <CardHeader>
              <CardTitle>
                {story ? `Story — Version ${story.version}` : "Story"}
              </CardTitle>
              {story && (
                <Button variant="ghost" size="sm" onClick={copyStory}>
                  {copied ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
                  {copied ? "Copied" : "Copy"}
                </Button>
              )}
            </CardHeader>
            <CardContent className="p-5">
              {busy === "generate" && (
                <div className="space-y-3">
                  <p className="text-sm text-muted-foreground">
                    Story Director → Writer → Critic pipeline is running. This can take a few minutes…
                  </p>
                  <Skeleton className="h-5 w-3/4" />
                  <Skeleton className="h-5 w-full" />
                  <Skeleton className="h-5 w-5/6" />
                  <Skeleton className="h-5 w-full" />
                  <Skeleton className="h-5 w-2/3" />
                </div>
              )}
              {busy !== "generate" && error && (
                <div className="rounded-md border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-600 dark:text-rose-400">
                  {error}
                </div>
              )}
              {busy !== "generate" && !error && !story && (
                <EmptyState
                  title="No story generated"
                  description="Choose language, duration and tone, then run the pipeline. The story appears here — clean, without citations or sources."
                />
              )}
              {busy !== "generate" && story && <StoryReader story={story} />}
              {busy === "improve" && story && (
                <p className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground">
                  Revising… a new version will appear in the list.
                </p>
              )}
            </CardContent>
          </Card>

          <Card className="h-fit">
            <CardHeader>
              <CardTitle>Story Quality</CardTitle>
            </CardHeader>
            <CardContent>
              {story ? (
                <QualityPanel story={story} />
              ) : (
                <p className="text-xs text-muted-foreground">
                  Scores appear after the Engagement Critic evaluates a draft.
                </p>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
