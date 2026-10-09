import type {
  CaseNaming,
  EpisodeIdentity,
  NamingLanguage,
  TitleCandidate,
  AgentRun,
  ApimasterStatus,
  ApproveFollowUpPayload,
  ArchiveFilter,
  ArchiveResponse,
  CaseAudit,
  CaseDetail,
  CaseResolution,
  CreateCasePayload,
  CreatedCase,
  DuplicateConflict,
  Film,
  FilmListItem,
  FollowUpCandidate,
  FollowUpState,
  MonitorRun,
  MonitorStatus,
  PublishFilmPayload,
  ResolutionFields,
  ResolutionStatus,
  SetStatusPayload,
  StatusCheck,
  SuggestionRecord,
  SuggestionState,
  CaseDossier,
  CaseListItem,
  ClaimClusterItem,
  Contradiction,
  CorpusSearchResponse,
  DashboardResponse,
  DbOverview,
  ResearchProviderStatus,
  SearchEngineStatus,
  DiscoveryRequest,
  DynamicEQSettings,
  EQPreviewResponse,
  DocumentaryBatch,
  DocumentaryBatchRequest,
  DocumentaryBatchStart,
  DocumentaryJob,
  DocumentaryJobListItem,
  DocumentaryJobRequest,
  DocumentaryOverview,
  DocumentaryScheduler,
  DocumentarySettings,
  HostScene,
  ChapterPlanRecord,
  StudioPatch,
  StudiosResponse,
  StudioValidation,
  Fact,
  JobStartResponse,
  LocalizationCompare,
  MasterStoryResponse,
  MusicTrack,
  NarrativeCapacity,
  Production,
  ResearchDepth,
  ResearchJob,
  ResearchQueryItem,
  ResearchLanguages,
  SourceDetail,
  SettingsStatus,
  Source,
  SourceChunk,
  SpeechStructure,
  StoryFull,
  StoryMeta,
  CaseStatus,
  VideoDetail,
  VideoSourceItem,
  VisualAsset,
  VisualFilters,
  VisualUpdate,
  VisualUpload,
  VoiceDynamics,
  VoiceManifest,
  VoicePerformance,
} from "./types";

import { API_BASE } from "./config";

export class ApiError extends Error {
  status: number;
  /** The response's `detail` when it is structured (e.g. a 409 duplicate verdict). */
  data: unknown;
  constructor(status: number, detail: string, data?: unknown) {
    super(detail);
    this.status = status;
    this.data = data ?? null;
  }
}

/**
 * The duplicate checker's verdict when a create/investigate call was
 * refused with 409 because the same real-world case already exists —
 * null for any other error. Retry with `force` to create it anyway.
 */
export function duplicateConflict(e: unknown): DuplicateConflict | null {
  if (!(e instanceof ApiError) || e.status !== 409) return null;
  const d = e.data;
  if (d && typeof d === "object" && typeof (d as { reason?: unknown }).reason === "string") {
    return d as DuplicateConflict;
  }
  return null;
}

/**
 * Default client-side timeout. A hung backend must surface as an error state
 * with a retry — never an infinite skeleton. GETs are expected to resolve
 * quickly (provider-backed polling endpoints may take up to ~60s server-side),
 * so 45s is a safe ceiling. Mutations (story generation can run for many
 * minutes) opt out by passing `timeoutMs: 0`.
 */
const DEFAULT_GET_TIMEOUT_MS = 45_000;
const DEFAULT_MUTATION_TIMEOUT_MS = 10 * 60 * 1000;

async function request<T>(
  path: string,
  init?: RequestInit & { timeoutMs?: number },
): Promise<T> {
  const { timeoutMs, ...rest } = init ?? {};
  const isMutation = rest.method != null && rest.method !== "GET" && rest.method !== "HEAD";
  const ms = timeoutMs ?? (isMutation ? DEFAULT_MUTATION_TIMEOUT_MS : DEFAULT_GET_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...rest,
      signal: ms > 0 ? AbortSignal.timeout(ms) : rest.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError") {
      throw new ApiError(
        0,
        `The API did not respond within ${Math.round(ms / 1000)}s. It may be busy — retry, or check that the backend is healthy.`,
      );
    }
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, `Cannot reach API at ${API_BASE}. Is the backend running?`);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    let data: unknown = null;
    try {
      const body = await res.json();
      data = body?.detail ?? null;
      if (typeof body?.detail === "string") detail = body.detail;
      else if (typeof body?.detail?.message === "string")
        detail = body.detail.message;
      else if (Array.isArray(body?.detail))
        detail = body.detail.map((d: { msg?: string }) => d.msg).join("; ");
    } catch {
      /* keep default detail */
    }
    throw new ApiError(res.status, detail, data);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? "{}" : JSON.stringify(body) });

const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });

const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });

/** "?a=1&b=2" from the set values, "" when none is set. */
function query(params: Record<string, string | number | null | undefined>): string {
  const qs = new URLSearchParams();
  for (const [key, value] of Object.entries(params))
    if (value != null && value !== "") qs.set(key, String(value));
  const s = qs.toString();
  return s ? `?${s}` : "";
}

/** Absolute URL for a file path the API returns relative (`/api/...`). */
export const apiFileUrl = (path: string) => `${API_BASE}${path}`;

/** Resolve a 404 to null — for resources that simply do not exist yet. */
export function nullIfNotFound<T>(promise: Promise<T>): Promise<T | null> {
  return promise.catch((e: unknown) =>
    e instanceof ApiError && e.status === 404 ? null : Promise.reject(e),
  );
}

export interface AddSourcePayload {
  title: string;
  url: string;
  source_type: string;
  language: string;
  publisher?: string | null;
  raw_text?: string | null;
  notes?: string | null;
  reliability_score?: number;
  is_authorized_text?: boolean;
}

export const api = {
  dashboard: () => request<DashboardResponse>("/api/dashboard"),

  discover: (payload: DiscoveryRequest) =>
    post<JobStartResponse>("/api/topics/discover", payload),
  /** 409 (see `duplicateConflict`) when the case already exists — `force` creates it anyway. */
  investigate: (candidateId: number, language = "fa", force = false) =>
    post<CreatedCase>(`/api/discovery/${candidateId}/investigate`, { language, force }),
  ignoreCandidate: (candidateId: number) =>
    post<{ id: number; rejected: boolean }>(`/api/discovery/${candidateId}/ignore`),
  /** Every stored suggestion — also the duplicates/filtered ones, with their reasons. */
  discoveryHistory: (state?: SuggestionState) =>
    request<SuggestionRecord[]>(`/api/discovery/history${query({ state })}`),

  listCases: (params?: { status?: string; q?: string; resolution?: ResolutionStatus | "ALL" | "" }) =>
    request<CaseListItem[]>(
      `/api/cases${query({ status: params?.status, q: params?.q, resolution: params?.resolution })}`,
    ),
  /** 409 (see `duplicateConflict`) when the case already exists — `force` creates it anyway. */
  createCase: (payload: CreateCasePayload) => post<CreatedCase>("/api/cases", payload),
  getCase: (id: number) => request<CaseDetail>(`/api/cases/${id}`),
  updateCase: (id: number, payload: { status?: CaseStatus }) =>
    patch<{ id: number; status: CaseStatus } & Partial<ResolutionFields>>(`/api/cases/${id}`, payload),

  // Case lifecycle: resolution status, monitor, films, archive, follow-ups, audit
  caseResolution: (caseId: number) => request<CaseResolution>(`/api/cases/${caseId}/status`),
  setCaseResolution: (caseId: number, payload: SetStatusPayload) =>
    put<CaseResolution>(`/api/cases/${caseId}/status`, payload),
  caseStatusChecks: (caseId: number) =>
    request<StatusCheck[]>(`/api/cases/${caseId}/status-checks`),
  caseAudit: (caseId: number) => request<CaseAudit>(`/api/cases/${caseId}/audit`),

  // Case naming + episode identity
  caseNaming: (caseId: number) => request<CaseNaming>(`/api/cases/${caseId}/naming`),
  generateNaming: (caseId: number, languages?: NamingLanguage[]) =>
    post<unknown>(`/api/cases/${caseId}/naming/generate`, { languages: languages ?? null }),
  manualTitle: (caseId: number, language: NamingLanguage, title: string) =>
    post<TitleCandidate>(`/api/cases/${caseId}/naming/manual`, { language, title }),
  approveTitle: (caseId: number, candidateId: number, revise = false) =>
    post<EpisodeIdentity>(`/api/cases/${caseId}/naming/${candidateId}/approve`, { revise }),

  monitorStatus: () => request<MonitorStatus>("/api/monitor"),
  /** Starts a monitor run in the background (202); follow it via `monitorStatus`. */
  runMonitor: () => post<{ started: boolean }>("/api/monitor/run"),
  monitorRun: (runId: number) =>
    request<MonitorRun & { checks: StatusCheck[] }>(`/api/monitor/runs/${runId}`),

  listFilms: (params?: { case_id?: number; state?: string; status?: string; language?: string }) =>
    request<FilmListItem[]>(
      `/api/films${query({
        case_id: params?.case_id,
        state: params?.state,
        status: params?.status,
        language: params?.language,
      })}`,
    ),
  getFilm: (videoId: number) =>
    request<FilmListItem & { follow_ups: Film[] }>(`/api/films/${videoId}`),
  publishFilm: (videoId: number, payload: PublishFilmPayload) =>
    post<FilmListItem & { follow_ups: Film[] }>(`/api/films/${videoId}/publish`, payload),
  archiveFilm: (videoId: number) =>
    post<FilmListItem & { follow_ups: Film[] }>(`/api/films/${videoId}/archive`),
  archive: (status: ArchiveFilter = "ALL") =>
    request<ArchiveResponse>(`/api/archive${query({ status })}`),

  followUps: (state: FollowUpState | "all" = "pending") =>
    request<FollowUpCandidate[]>(`/api/follow-ups${query({ state })}`),
  /** The user's yes: starts exactly one follow-up documentary job. */
  approveFollowUp: (id: number, payload: ApproveFollowUpPayload) =>
    post<{ follow_up: FollowUpCandidate; job: DocumentaryJob }>(`/api/follow-ups/${id}/approve`, payload),
  dismissFollowUp: (id: number) => post<FollowUpCandidate>(`/api/follow-ups/${id}/dismiss`),

  listSources: (caseId: number) => request<Source[]>(`/api/cases/${caseId}/sources`),
  corpusSearch: (caseId: number, q: string, language?: string, limit = 10) => {
    const params = new URLSearchParams({ q, limit: String(limit) });
    if (language) params.set("language", language);
    return request<CorpusSearchResponse>(
      `/api/cases/${caseId}/corpus-search?${params.toString()}`,
      { timeoutMs: 120_000 },
    );
  },
  caseSource: (caseId: number, sourceId: number) =>
    request<SourceDetail>(`/api/cases/${caseId}/sources/${sourceId}`),
  addSource: (caseId: number, payload: AddSourcePayload) =>
    post<{ id: number }>(`/api/cases/${caseId}/sources`, payload),
  deleteSource: (caseId: number, sourceId: number) =>
    request<{ id: number; deleted: boolean }>(`/api/cases/${caseId}/sources/${sourceId}`, {
      method: "DELETE",
    }),

  runResearch: (caseId: number) =>
    post<JobStartResponse>(`/api/cases/${caseId}/research`),
  researchJob: (jobId: number) => request<ResearchJob>(`/api/research-jobs/${jobId}`),
  researchJobQueries: (jobId: number) =>
    request<ResearchQueryItem[]>(`/api/research-jobs/${jobId}/queries`),
  listResearchJobs: (caseId?: number) =>
    request<ResearchJob[]>(
      `/api/research-jobs${caseId != null ? `?case_id=${caseId}` : ""}`,
    ),
  researchProviderStatus: () =>
    request<ResearchProviderStatus>("/api/integrations/research/status"),
  searchEngineStatus: () =>
    request<SearchEngineStatus>("/api/integrations/research/status"),
  apimasterStatus: () =>
    request<ApimasterStatus>("/api/integrations/apimaster/status"),
  listFacts: (caseId: number) => request<Fact[]>(`/api/cases/${caseId}/facts`),
  listTimeline: (caseId: number) => request<Fact[]>(`/api/cases/${caseId}/timeline`),
  listContradictions: (caseId: number) =>
    request<Contradiction[]>(`/api/cases/${caseId}/contradictions`),

  generateStory: (
    caseId: number,
    payload: { target_minutes: number; language: string; tone: string; iterations?: number },
  ) =>
    post<{
      story_version_id: number;
      version: number;
      engagement_score: number;
      similarity_score: number | null;
      similarity_status: string;
      status: string;
      story_text: string;
    }>(`/api/cases/${caseId}/generate-story`, payload),
  improveStory: (caseId: number, storyVersionId: number, instruction: string) =>
    post<StoryFull>(`/api/cases/${caseId}/improve-story`, {
      story_version_id: storyVersionId,
      instruction,
    }),
  listStories: (caseId: number) => request<StoryMeta[]>(`/api/cases/${caseId}/stories`),
  getStoryVersion: (caseId: number, versionId: number) =>
    request<StoryFull>(`/api/cases/${caseId}/stories/${versionId}`),

  researchLanguages: (caseId: number) =>
    request<ResearchLanguages>(`/api/cases/${caseId}/research/languages`),
  researchCapacity: (caseId: number, targetMinutes = 45) =>
    request<NarrativeCapacity>(
      `/api/cases/${caseId}/research/capacity?target_minutes=${targetMinutes}`,
    ),
  researchDepth: (caseId: number, targetMinutes = 45) =>
    request<ResearchDepth>(
      `/api/cases/${caseId}/research/depth?target_minutes=${targetMinutes}`,
    ),
  sourceChunks: (caseId: number, sourceId: number) =>
    request<SourceChunk[]>(`/api/cases/${caseId}/sources/${sourceId}/chunks`),
  researchFollowup: (caseId: number, targetMinutes = 45) =>
    request<{ job_id: number; gap_plan: unknown }>(
      `/api/cases/${caseId}/research/followup?target_minutes=${targetMinutes}`,
      { method: "POST" },
    ),
  getMasterStory: (caseId: number) =>
    request<MasterStoryResponse>(`/api/cases/${caseId}/master-story`),
  generateMasterStory: (
    caseId: number,
    payload: { target_minutes: number; language: string; tone: string; iterations?: number },
  ) =>
    post<StoryFull>(`/api/cases/${caseId}/master-story/generate`, payload),
  listLocalizations: (caseId: number) =>
    request<StoryMeta[]>(`/api/cases/${caseId}/localizations`),
  generateLocalization: (caseId: number, language: string, targetMinutes?: number) =>
    post<StoryFull>(
      `/api/cases/${caseId}/localizations/generate?language=${language}` +
        (targetMinutes != null ? `&target_minutes=${targetMinutes}` : ""),
    ),
  getLocalization: (versionId: number) =>
    request<StoryFull>(`/api/localizations/${versionId}`),
  improveLocalization: (versionId: number) =>
    post<StoryFull>(`/api/localizations/${versionId}/improve`),
  compareMaster: (versionId: number) =>
    request<LocalizationCompare>(`/api/localizations/${versionId}/compare-master`),

  startVideoResearch: (caseId: number) =>
    post<JobStartResponse>(`/api/cases/${caseId}/video-research`),
  listVideos: (caseId: number) =>
    request<VideoSourceItem[]>(`/api/cases/${caseId}/videos`),
  getVideo: (caseId: number, videoId: number) =>
    request<VideoDetail>(`/api/cases/${caseId}/videos/${videoId}`),
  listClaimClusters: (caseId: number) =>
    request<ClaimClusterItem[]>(`/api/cases/${caseId}/claim-clusters`),
  getDossier: (caseId: number) =>
    request<CaseDossier>(`/api/cases/${caseId}/dossier`),

  caseAgentRuns: (caseId: number) => request<AgentRun[]>(`/api/cases/${caseId}/agent-runs`),
  allAgentRuns: () => request<AgentRun[]>("/api/agent-runs"),

  settingsStatus: () => request<SettingsStatus>("/api/settings/status"),
  dbOverview: () => request<DbOverview>("/api/db/overview"),

  // Documentary production
  documentarySettings: () => request<DocumentarySettings>("/api/documentary/settings"),
  studios: () => request<StudiosResponse>("/api/studios"),
  updateStudio: (language: string, payload: StudioPatch) =>
    patch<StudiosResponse & { validation: StudioValidation }>(`/api/studios/${language}`, payload),
  syncStudios: () =>
    post<{ changed: { id: string }[]; validation: Record<string, StudioValidation> }>(
      "/api/studios/sync",
    ),
  /** Chapter titles, film title and timeline labels (null before the first run). */
  chapters: (caseId: number, versionId?: number | null) =>
    nullIfNotFound(
      request<ChapterPlanRecord>(
        `/api/cases/${caseId}/documentary/chapters${query({ version_id: versionId ?? undefined })}`,
      ),
    ),
  hostScenes: (params: { case_id?: number; language?: string } = {}) =>
    request<HostScene[]>(`/api/host-scenes${query(params)}`),
  runHostScene: (id: number, until: "voice" | "avatar") =>
    post<HostScene>(`/api/host-scenes/${id}/run`, { until }),
  documentaryOverview: (caseId: number, versionId?: number | null) =>
    request<DocumentaryOverview>(
      `/api/cases/${caseId}/documentary${versionId != null ? `?version_id=${versionId}` : ""}`,
    ),
  startDocumentaryJob: (caseId: number, payload: DocumentaryJobRequest) =>
    post<DocumentaryJob>(`/api/cases/${caseId}/documentary/jobs`, payload),
  documentaryJob: (jobId: number) => request<DocumentaryJob>(`/api/documentary/jobs/${jobId}`),
  listDocumentaryJobs: (limit = 20) =>
    request<DocumentaryJobListItem[]>(`/api/documentary/jobs?limit=${limit}`),
  startDocumentaryBatch: (payload: DocumentaryBatchRequest) =>
    post<DocumentaryBatchStart>("/api/documentary/batch", payload),
  documentaryBatch: (batchId: string) =>
    request<DocumentaryBatch>(`/api/documentary/batches/${encodeURIComponent(batchId)}`),
  documentaryScheduler: () => request<DocumentaryScheduler>("/api/documentary/scheduler"),
  cancelDocumentaryJob: (jobId: number) =>
    post<DocumentaryJob>(`/api/documentary/jobs/${jobId}/cancel`),
  resumeDocumentaryJob: (jobId: number) =>
    post<DocumentaryJob>(`/api/documentary/jobs/${jobId}/resume`),
  documentaryProduction: (caseId: number, language: string, versionId?: number | null) =>
    request<Production>(
      `/api/cases/${caseId}/documentary/production/${language}` +
        (versionId != null ? `?version_id=${versionId}` : ""),
    ),
  musicLibrary: () => request<MusicTrack[]>("/api/documentary/music"),
  storyVoice: (caseId: number, versionId: number) =>
    request<VoiceManifest>(`/api/cases/${caseId}/stories/${versionId}/voice`),
  voiceDynamics: (caseId: number, versionId: number) =>
    request<VoiceDynamics>(`/api/cases/${caseId}/stories/${versionId}/voice/dynamics`),
  eqPreview: (
    caseId: number,
    versionId: number,
    payload: {
      enabled?: boolean;
      strength?: number;
      max_atten_db?: number;
      deesser_enabled?: boolean;
      deesser_strength?: number;
      seconds?: number;
    },
  ) =>
    post<EQPreviewResponse>(
      `/api/cases/${caseId}/stories/${versionId}/voice/eq-preview`,
      payload,
    ),
  updateDynamicEq: (payload: {
    enabled?: boolean;
    strength?: number;
    max_atten_db?: number;
    deesser_enabled?: boolean;
    deesser_strength?: number;
  }) =>
    request<{ dynamic_eq: DynamicEQSettings; note: string }>(
      "/api/documentary/settings/dynamic-eq",
      { method: "PATCH", body: JSON.stringify(payload) },
    ),
  voicePerformance: (versionId: number) =>
    request<VoicePerformance>(`/api/documentary/versions/${versionId}/voice-performance`),
  // Directs every requested sentence with the model (a whole film is many
  // calls): no client timeout, like story generation.
  directVoicePerformance: (versionId: number, beatIds: string[] | null) =>
    request<VoicePerformance>(`/api/documentary/versions/${versionId}/voice-performance`, {
      method: "POST",
      body: JSON.stringify({ beat_ids: beatIds }),
      timeoutMs: 0,
    }),
  speechStructure: (versionId: number) =>
    request<SpeechStructure>(`/api/documentary/versions/${versionId}/speech`),

  listVisuals: (caseId: number, filters: VisualFilters = {}) => {
    const qs = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) if (value) qs.set(key, value);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<VisualAsset[]>(`/api/cases/${caseId}/visuals${suffix}`);
  },
  updateVisual: (assetId: number, payload: VisualUpdate) =>
    patch<VisualAsset>(`/api/visuals/${assetId}`, payload),
  /** Cut a kept video by meaning again (when the segmenter failed). */
  cutVideoAgain: (assetId: number) =>
    request<VisualAsset & { pieces: number }>(`/api/visuals/${assetId}/cut-again`, {
      method: "POST",
    }),
  uploadVisual: (caseId: number, upload: VisualUpload) => {
    const form = new FormData();
    form.set("file", upload.file);
    if (upload.title.trim()) form.set("title", upload.title.trim());
    if (upload.caption.trim()) form.set("caption", upload.caption.trim());
    form.set("role", upload.role);
    form.set("rights", upload.rights);
    if (upload.start && upload.start > 0) form.set("start", String(upload.start));
    // Empty headers drop the JSON content type: the browser sets the
    // multipart boundary itself.
    return request<VisualAsset>(`/api/cases/${caseId}/visuals/upload`, {
      method: "POST",
      body: form,
      headers: {},
    });
  },
};

export interface PollOptions {
  intervalMs?: number;
  timeoutMs?: number;
  /** Called on every poll cycle with the latest job snapshot. */
  onUpdate?: (job: ResearchJob) => void;
  /** Abort the polling loop (e.g. on component unmount). */
  signal?: AbortSignal;
}

export async function pollResearchJob(
  jobId: number,
  { intervalMs = 4000, timeoutMs = 15 * 60 * 1000, onUpdate, signal }: PollOptions = {},
): Promise<ResearchJob> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const job = await api.researchJob(jobId);
    onUpdate?.(job);
    if (job.status === "completed" || job.status === "failed") return job;
    if (Date.now() > deadline) {
      throw new ApiError(0, "Research job timed out. Check the Research page for its status.");
    }
    await new Promise((r, reject) => {
      const t = setTimeout(r, intervalMs);
      signal?.addEventListener(
        "abort",
        () => {
          clearTimeout(t);
          reject(new DOMException("Aborted", "AbortError"));
        },
        { once: true },
      );
    });
  }
}
