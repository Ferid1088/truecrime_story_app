"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { AlertTriangle, Check, Clock3, Lock, PenLine, Play, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { langLabel } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type {
  DocumentaryJob,
  DocumentaryStage,
  StageControlAction,
  StageControlOperationResult,
  StageControlPreview,
  StageControlState,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState, ErrorState } from "@/components/state";
import { stageLabel, summarizeDetail } from "./shared";

const ACTIONS: StageControlAction[] = ["continue", "redo_with_prompt", "edit"];

function statusVariant(status: string): "success" | "warning" | "danger" | "info" | "outline" {
  if (status === "done" || status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "running" || status === "queued") return "warning";
  if (status === "skipped") return "info";
  return "outline";
}

function stageLanguage(stage: string): string | null {
  const index = stage.lastIndexOf(":");
  return index > -1 ? stage.slice(index + 1) : null;
}

function actionLabel(action: StageControlAction): string {
  if (action === "continue") return "Continue";
  if (action === "redo_with_prompt") return "Redo with prompt";
  if (action === "edit") return "Edit";
  return "Run automatically";
}

interface PreviewDialogProps {
  preview: StageControlPreview | null;
  stale: boolean;
  applying: boolean;
  error: string | null;
  onApply: () => void;
  onClose: () => void;
}

export function PreviewDialog({ preview, stale, applying, error, onApply, onClose }: PreviewDialogProps) {
  return (
    <Dialog
      open={preview != null}
      onClose={onClose}
      title="Exact preview scope"
      description={preview ? `${stageLabel(preview.stage)} · ${actionLabel(preview.action)}` : undefined}
      className="max-h-[88vh] overflow-y-auto"
    >
      {preview && (
        <div className="space-y-4" data-testid="preview-dialog">
          <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            <Metric label="Artifacts" value={preview.estimated_scope.length} />
            <Metric label="Languages" value={preview.languages.map(langLabel).join(", ") || "—"} />
            <Metric label="Estimated provider calls" value={preview.estimated_provider_calls.total} />
            <Metric label="Preview ID" value={preview.preview_id} />
          </div>
          <div>
            <p className="mb-2 text-xs font-medium">Exact preview scope</p>
            {preview.estimated_scope.length === 0 ? (
              <p className="rounded-md border border-dashed border-border px-3 py-3 text-xs text-muted-foreground">
                No pipeline stages are available yet.
              </p>
            ) : (
              <ul className="max-h-56 divide-y divide-border overflow-y-auto rounded-md border border-border">
                {preview.estimated_scope.map((item) => (
                  <li key={item.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs">
                    <span className="min-w-0">
                      <span className="font-medium">{item.type}</span>
                      <span className="text-muted-foreground"> · {item.stage ?? "—"}</span>
                    </span>
                    <span className="flex items-center gap-2">
                      {item.language && <Badge variant="outline">{langLabel(item.language)}</Badge>}
                      {item.may_change && <Badge variant="warning">may change</Badge>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          {Object.keys(preview.estimated_provider_calls.by_stage).length > 0 && (
            <div className="rounded-md bg-muted/50 px-3 py-2 text-xs">
              <p className="font-medium">Estimated provider calls</p>
              <p className="mt-1 text-muted-foreground">
                {Object.entries(preview.estimated_provider_calls.by_stage)
                  .map(([stage, calls]) => `${stage}: ${calls}`)
                  .join(" · ")}
              </p>
            </div>
          )}
          {stale && <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">inputs changed — preview again</p>}
          {error && <p role="alert" className="rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button onClick={onApply} disabled={stale} loading={applying} data-testid="apply-preview">
              <Check /> Apply
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <div className="rounded-md border border-border px-2.5 py-2"><p className="font-semibold tabular-nums">{value}</p><p className="mt-1 text-[10px] text-muted-foreground">{label}</p></div>;
}

interface ActionPanelProps {
  caseId: number;
  state: StageControlState;
  onPreview: (preview: StageControlPreview) => void;
  onAutomatic: (enabled: boolean) => Promise<void>;
}

export function ActionPanel({ caseId, state, onPreview, onAutomatic }: ActionPanelProps) {
  const router = useRouter();
  const [prompt, setPrompt] = useState("");
  const [chapterText, setChapterText] = useState("");
  const [chapterIndex, setChapterIndex] = useState("0");
  const [editLanguage, setEditLanguage] = useState(state.artifacts.find((a) => a.language)?.language ?? "de");
  const [openAction, setOpenAction] = useState<StageControlAction | null>(null);
  const [busy, setBusy] = useState<StageControlAction | null>(null);
  const [automaticBusy, setAutomaticBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const requestPreview = async (action: StageControlAction) => {
    setBusy(action);
    setError(null);
    try {
      const edit = action === "edit"
        ? { language: editLanguage, chapter_index: Number(chapterIndex), chapter_text: chapterText, text: chapterText, artifact_type: state.artifacts[0]?.type }
        : undefined;
      const preview = await api.stageControlPreview(caseId, {
        stage: state.stage,
        action,
        prompt: action === "redo_with_prompt" ? prompt : undefined,
        edit,
      });
      onPreview(preview);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const editArtifact = state.artifacts[0];
  const editable = new Set(["spoken_version", "chapter_plan", "chapter_cards", "timeline_cards"]);
  const canEdit = !!editArtifact && editable.has(editArtifact.type);
  const editTitle = editArtifact?.type === "naming" ? "Edit naming" :
    editArtifact?.type === "visual_asset" ? "Edit thumbnail brief" :
    editArtifact?.type === "audio_blocks" ? "Edit audio plan" :
    editArtifact?.type === "chapter_cards" || editArtifact?.type === "chapter_plan" || editArtifact?.type === "timeline_cards" ? "Edit chapter cards" :
    "Edit spoken chapter text";
  const structuredEditor = editArtifact?.type === "naming" ? { href: `/cases/${caseId}?tab=Naming`, label: "Open naming editor" } :
    editArtifact?.type === "visual_asset" ? { href: `/cases/${caseId}?tab=Thumbnail`, label: "Open thumbnail editor" } :
    editArtifact?.type === "audio_blocks" ? { href: `/documentary/${caseId}`, label: "Open audio plan editor" } : null;
  const changeAutomatic = async (enabled: boolean) => {
    setAutomaticBusy(true);
    setError(null);
    try { await onAutomatic(enabled); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setAutomaticBusy(false); }
  };
  return (
    <div className="mt-3 space-y-3 border-t border-border pt-3">
      <div className="flex flex-wrap gap-2">
        {ACTIONS.map((action) => {
          const blocked = state.blocked_actions[action];
          const active = openAction === action;
          return (
            <div key={action} className="min-w-[8.5rem] flex-1 sm:flex-none">
              <Button
                variant={active ? "secondary" : "outline"}
                size="sm"
                className="w-full"
                disabled={!!blocked}
                loading={busy === action}
                onClick={() => action === "continue" ? requestPreview(action) : setOpenAction(active ? null : action)}
                title={blocked ?? undefined}
                data-testid={`action-${action}`}
              >
                {action === "continue" && <Play />}
                {action === "redo_with_prompt" && <RotateCcw />}
                {action === "edit" && <PenLine />}
                {actionLabel(action)}
              </Button>
              {blocked && <p className="mt-1 flex items-start gap-1 text-[10px] text-muted-foreground"><Lock className="mt-0.5 size-3 shrink-0" />{blocked}</p>}
            </div>
          );
        })}
      </div>
      {openAction === "redo_with_prompt" && (
        <div className="space-y-2 rounded-md border border-border bg-muted/20 p-3">
          <label className="block text-xs font-medium" htmlFor={`prompt-${state.stage}`}>Prompt</label>
          <Textarea id={`prompt-${state.stage}`} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="What should change?" rows={3} />
          <Button size="sm" onClick={() => requestPreview("redo_with_prompt")} disabled={!prompt.trim()} loading={busy === "redo_with_prompt"}>Preview</Button>
        </div>
      )}
      {openAction === "edit" && (
        <div className="space-y-2 rounded-md border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">{canEdit ? `${editTitle}. Preview the exact downstream scope before applying it.` : "This stage has no supported editor."}</p>
          {canEdit ? (
            <>
              <div className="grid gap-2 sm:grid-cols-2">
                <label className="text-xs font-medium">Language<Select value={editLanguage} onChange={(event) => setEditLanguage(event.target.value)} className="mt-1"><option value="de">{langLabel("de")}</option><option value="en">{langLabel("en")}</option><option value="fa">{langLabel("fa")}</option><option value="ar">{langLabel("ar")}</option></Select></label>
                <label className="text-xs font-medium">Chapter<input className="mt-1 flex h-8 w-full rounded-md border border-border bg-transparent px-3 py-1 text-sm" type="number" min="0" value={chapterIndex} onChange={(event) => setChapterIndex(event.target.value)} /></label>
              </div>
              <Textarea aria-label="Edit text" value={chapterText} onChange={(event) => setChapterText(event.target.value)} placeholder="Enter the edited chapter text" rows={5} />
              <Button size="sm" onClick={() => requestPreview("edit")} disabled={!chapterText.trim()} loading={busy === "edit"}>Save edit and preview</Button>
            </>
          ) : structuredEditor ? (
            <div className="space-y-2"><p className="text-xs text-muted-foreground">Structured edits are not accepted as free text. Open the specialized editor; its save must return through preview and apply.</p><Button size="sm" variant="outline" onClick={() => router.push(structuredEditor.href)}>{structuredEditor.label}</Button></div>
          ) : <p className="text-xs text-muted-foreground">This artifact has no supported editor.</p>}
        </div>
      )}
      {error && <p role="alert" className="text-xs text-rose-600 dark:text-rose-400">The action could not be prepared. {error}</p>}
      <Checkbox checked={state.automatic} disabled={automaticBusy} onChange={(event) => void changeAutomatic(event.target.checked)} label="Run automatically" />
    </div>
  );
}

interface StageCardProps {
  stage: DocumentaryStage;
  state: StageControlState | null;
  error: string | null;
  loading: boolean;
  caseId: number;
  onPreview: (preview: StageControlPreview) => void;
  onAutomatic: (stage: string, enabled: boolean) => Promise<void>;
}

export function StageCard({ stage, state, error, loading, caseId, onPreview, onAutomatic }: StageCardProps) {
  const language = stageLanguage(stage.name);
  const detail = summarizeDetail(stage.detail);
  return (
    <Card data-testid={`stage-card-${stage.name}`}>
      <CardHeader className="items-start gap-3">
        <div className="min-w-0">
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span>{stageLabel(stage.name)}</span>
            <Badge variant={statusVariant(state?.status ?? stage.status)}>{state?.status ?? stage.status}</Badge>
          </CardTitle>
          <div className="mt-1 flex flex-wrap gap-1">
            {language && <Badge variant="outline">{langLabel(language)}</Badge>}
            {state?.artifacts.map((artifact) => artifact.language && <Badge key={`${artifact.id}-${artifact.language}`} variant="outline">{langLabel(artifact.language)}</Badge>)}
          </div>
        </div>
        {stage.finished_at && <span className="flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground"><Clock3 className="size-3" />{new Date(stage.finished_at).toLocaleString()}</span>}
      </CardHeader>
      <CardContent>
        {detail && <p className="mb-2 text-xs text-muted-foreground">{detail}</p>}
        {loading && <div className="space-y-2"><Skeleton className="h-8 w-full" /><Skeleton className="h-4 w-2/3" /></div>}
        {error && <ErrorState message={error} />}
        {!loading && !error && state && <ActionPanel caseId={caseId} state={state} onPreview={onPreview} onAutomatic={(enabled) => onAutomatic(state.stage, enabled)} />}
        {!loading && !error && !state && <p className="text-xs text-muted-foreground">{typeof stage.detail === "string" ? stage.detail : "No pipeline stages are available yet."}</p>}
      </CardContent>
    </Card>
  );
}

interface StageListProps {
  caseId: number;
  job: DocumentaryJob | null;
  language: string;
  onPreview: (preview: StageControlPreview) => void;
  unavailableStages?: DocumentaryStage[];
}

export function StageList({ caseId, job, language, onPreview, unavailableStages = [] }: StageListProps) {
  const stages = useMemo(() => job?.stages ?? unavailableStages, [job?.stages, unavailableStages]);
  const stageNames = useMemo(() => job?.stages.map((stage) => stage.name) ?? [], [job?.stages]);
  const [automaticBusy, setAutomaticBusy] = useState(false);
  const stateRequest = useApi(
    () => Promise.all(stageNames.map(async (name) => {
      try { return [name, await api.stageControlState(caseId, name)] as const; }
      catch (e) { return [name, e instanceof Error ? e.message : String(e)] as const; }
    })),
    [caseId, stageNames.join("|")],
    { keepPrevious: true },
  );
  const states = useMemo(() => Object.fromEntries((stateRequest.data ?? []).filter(([, value]) => typeof value !== "string")) as Record<string, StageControlState>, [stateRequest.data]);
  const errors = useMemo(() => Object.fromEntries((stateRequest.data ?? []).filter(([, value]) => typeof value === "string")) as Record<string, string>, [stateRequest.data]);

  const filtered = stages.filter((stage) => {
    if (language === "all") return true;
    const stageLang = stageLanguage(stage.name);
    return stageLang == null || stageLang === language || states[stage.name]?.artifacts.some((artifact) => artifact.language === language);
  });

  const setAutomatic = async (stage: string, enabled: boolean) => {
    await api.setStageAutomatic(caseId, stage, enabled);
    stateRequest.refetch();
  };

  const setAllAutomatic = async (enabled: boolean) => {
    setAutomaticBusy(true);
    try { await Promise.all(stageNames.map((stage) => api.setStageAutomatic(caseId, stage, enabled))); stateRequest.refetch(); }
    finally { setAutomaticBusy(false); }
  };

  if (!stages.length) return <EmptyState title="No pipeline stages are available yet." description="Preview the exact rebuild scope before applying any stage action." />;
  return (
    <div className="space-y-4">
      {job && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card px-4 py-3">
        <div><p className="text-sm font-medium">Run the whole case automatically</p><p className="text-xs text-muted-foreground">Preview the exact rebuild scope before applying any stage action.</p></div>
        <Checkbox disabled={automaticBusy} onChange={(event) => void setAllAutomatic(event.target.checked)} label={automaticBusy ? "Loading" : "Run automatically"} />
      </div>}
      <div className="grid gap-3 xl:grid-cols-2">
        {filtered.map((stage) => <StageCard key={stage.name} stage={stage} state={states[stage.name] ?? null} error={errors[stage.name] ?? null} loading={stateRequest.loading && !stateRequest.data} caseId={caseId} onPreview={onPreview} onAutomatic={setAutomatic} />)}
      </div>
      {!filtered.length && <EmptyState title="No pipeline stages are available yet." />}
      <Button variant="ghost" size="sm" onClick={stateRequest.refetch} loading={stateRequest.loading}><RotateCcw />Retry</Button>
    </div>
  );
}

interface AuditTimelineProps { caseId: number; stages: string[]; language: string; }

export function StageAuditTimeline({ caseId, stages, language }: AuditTimelineProps) {
  const [stage, setStage] = useState("");
  const audit = useApi(
    () => api.stageControlAudit(caseId, stage || undefined, language === "all" ? undefined : language),
    [caseId, language, stage],
    { keepPrevious: true },
  );
  return (
    <Card>
      <CardHeader><CardTitle>Audit timeline</CardTitle><Select aria-label="Stage" value={stage} onChange={(event) => setStage(event.target.value)} className="max-w-52"><option value="">All stages</option>{stages.map((name) => <option key={name} value={name}>{stageLabel(name)}</option>)}</Select></CardHeader>
      <CardContent>
        {audit.loading && <div className="space-y-2"><Skeleton className="h-5 w-full" /><Skeleton className="h-5 w-4/5" /></div>}
        {audit.error && <ErrorState message={audit.error} onRetry={audit.refetch} />}
        {!audit.loading && !audit.error && !audit.data?.length && <p className="text-xs text-muted-foreground">No stage-control events yet.</p>}
        {!audit.loading && !audit.error && audit.data && audit.data.length > 0 && <ol className="space-y-3 border-l border-border pl-4">{audit.data.map((event) => <li key={event.id} className="relative text-xs"><span className="absolute -left-[1.35rem] top-0.5 size-2 rounded-full bg-primary" /><p className="font-medium">{event.rule_id} · {event.decision}</p><p className="text-muted-foreground">{event.reason}</p><p className="mt-0.5 text-[10px] text-muted-foreground">{event.stage ?? "—"}{event.language ? ` · ${langLabel(event.language)}` : ""} · {new Date(event.created_at).toLocaleString()}</p></li>)}</ol>}
      </CardContent>
    </Card>
  );
}

export function StageControlResultView({ operation }: { operation: StageControlOperationResult }) {
  const result = operation.result;
  const outside = result.changed_outside_scope ?? [];
  const failed = result.status === "failed" || outside.length > 0;
  return (
    <Card data-testid="stage-control-result" className={failed ? "border-rose-500/40" : undefined}>
      <CardHeader><CardTitle className="flex items-center gap-2">{failed ? <AlertTriangle className="size-4 text-rose-500" /> : <Check className="size-4 text-emerald-500" />}Apply result</CardTitle><Badge variant={failed ? "danger" : "success"}>{result.status}</Badge></CardHeader>
      <CardContent className="grid gap-2 text-xs sm:grid-cols-3">
        <Metric label="Previewed" value={result.previewed_artifact_ids.length} />
        <Metric label="Executed" value={result.executed_artifact_ids.length} />
        <Metric label="Changed outside scope" value={outside.length ? outside.join(", ") : "None"} />
        {result.error && <p role="alert" className="sm:col-span-3 text-rose-600 dark:text-rose-400">{result.error}</p>}
        {result.status === "queued" && <p className="sm:col-span-3 text-muted-foreground">Queued — following the job</p>}
      </CardContent>
    </Card>
  );
}

export function StageControlPanel({ caseId, job }: { caseId: number; job: DocumentaryJob | null }) {
  const [language, setLanguage] = useState("all");
  const [preview, setPreview] = useState<StageControlPreview | null>(null);
  const [operation, setOperation] = useState<StageControlOperationResult | null>(null);
  const [applying, setApplying] = useState(false);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stages = job?.stages.map((stage) => stage.name) ?? [];
  const languages = job?.languages ?? ["en", "de", "fa", "ar"];

  const apply = async () => {
    if (!preview) return;
    setApplying(true); setError(null); setStale(false);
    try {
      await api.stageControlApply(caseId, preview.preview_id);
      let latest = await api.stageControlResult(caseId, preview.preview_id);
      for (let i = 0; i < 30 && latest.result.status === "queued"; i += 1) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        latest = await api.stageControlResult(caseId, preview.preview_id);
      }
      setOperation(latest);
      setPreview(null);
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setError(message);
      if (message.toLowerCase().includes("changed since preview")) setStale(true);
    } finally { setApplying(false); }
  };

  return (
    <div className="space-y-5" data-testid="stage-control-panel">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h2 className="text-lg font-semibold">Stage control</h2><p className="mt-1 text-xs text-muted-foreground">Preview the exact rebuild scope before applying any stage action.</p></div>
        <label className="min-w-40 text-xs font-medium">Language<Select value={language} onChange={(event) => setLanguage(event.target.value)} className="mt-1"><option value="all">All languages</option>{languages.map((code) => <option key={code} value={code}>{langLabel(code)}</option>)}</Select></label>
      </div>
      {error && <p role="alert" className="rounded-md border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      <StageList caseId={caseId} job={job} language={language} onPreview={(next) => { setError(null); setStale(false); setOperation(null); setPreview(next); }} />
      {operation && <StageControlResultView operation={operation} />}
      <StageAuditTimeline caseId={caseId} stages={stages} language={language} />
      <PreviewDialog preview={preview} stale={stale} applying={applying} error={error} onApply={() => void apply()} onClose={() => { if (!applying) setPreview(null); }} />
    </div>
  );
}
