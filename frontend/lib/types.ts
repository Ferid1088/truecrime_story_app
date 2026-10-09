export type CaseStatus =
  | "new"
  | "researching"
  | "researched"
  | "writing"
  | "story_ready"
  | "completed"
  | "rejected"
  | "archived";

export type RunStatus = "waiting" | "running" | "completed" | "failed";

/**
 * Whether the real-world case is solved — a first-class case attribute
 * (app/lifecycle/status.py), separate from the workflow `status`.
 */
export type ResolutionStatus = "SOLVED" | "UNSOLVED" | "UNKNOWN" | "STATUS_UNDER_REVIEW";

/** `resolution_dict` of app/lifecycle/api.py — carried by every case payload. */
export interface ResolutionFields {
  resolution_status: ResolutionStatus;
  /** 0–1; null when nobody has judged it. */
  resolution_confidence: number | null;
  resolution_summary: string | null;
  resolution_checked_at: string | null;
}

/** How a case entered the system. */
export type CaseOrigin = "discovery" | "manual" | "follow_up";

export interface DashboardStats {
  total_cases: number;
  cases_researched: number;
  stories_completed: number;
  cases_waiting: number;
  sources_collected: number;
  facts_extracted: number;
  contradictions_found: number;
}

export interface AgentActivity {
  agent_name: string;
  status: RunStatus;
  started_at: string | null;
  duration_ms: number | null;
}

export interface GenerationUsage {
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  estimated_cost_usd: number | null;
}

export interface CaseListItem extends ResolutionFields {
  id: number;
  title: string;
  status: CaseStatus;
  language: string;
  created_at: string;
  last_activity: string;
  sources: number;
  facts: number;
  contradictions: number;
  story_versions: number;
  latest_story_version: number | null;
  engagement_score: number | null;
  generation_usage: GenerationUsage;
}

export interface CaseDetail extends ResolutionFields {
  id: number;
  title: string;
  slug: string;
  status: CaseStatus;
  /** Identity of the real-world incident (duplicate detection). */
  aliases: string[];
  people: string[];
  location: string | null;
  incident_date: string | null;
  latest_development_date: string | null;
  origin: CaseOrigin | null;
  language: string;
  summary: string | null;
  created_at: string;
  languages_found: string[];
  narrative_angle: string | null;
  sources: number;
  facts: number;
  contradictions: number;
  story_versions: number;
  latest_story_version: number | null;
  engagement_score: number | null;
  generation_usage: GenerationUsage;
}

export interface DashboardResponse {
  stats: DashboardStats;
  recent_cases: CaseListItem[];
  agent_activity: AgentActivity[];
  /** Previously covered UNSOLVED cases that are now SOLVED, waiting for a decision. */
  follow_up_candidates: FollowUpCandidate[];
  resolution_counts: Record<ResolutionStatus, number>;
}

export interface Source {
  id: number;
  case_id: number;
  title: string;
  url: string;
  source_type: string;
  language: string;
  declared_language: string | null;
  detected_language: string | null;
  language_confidence: number | null;
  language_detection_method: string | null;
  publisher: string | null;
  raw_text: string | null;
  notes: string | null;
  reliability_score: number;
  is_authorized_text: boolean;
  summary: string | null;
  summary_en: string | null;
  content_status: string;
  value_flags: string[];
  source_family: string | null;
  retrieval_method: string | null;
  retrieval_notes: string | null;
  chunk_count: number;
  raw_text_length: number;
  published_at: string | null;
  retrieved_at: string | null;
  research_provider: string | null;
  external_reference: string | null;
  status: string;
  created_at: string;
}

export interface Fact {
  id: number;
  case_id: number;
  claim: string;
  original_claim: string | null;
  original_language: string;
  narrative_value: string | null;
  evidence_strength: string | null;
  quote_status: string | null;
  speaker: string | null;
  people: string[];
  locations: string[];
  supporting_text: string | null;
  category: string;
  confidence: number;
  disputed: boolean;
  event_date: string | null;
  source_ids: number[];
  chunk_ids: number[];
}

export interface Contradiction {
  id: number;
  case_id: number;
  topic: string;
  description: string;
  severity: string;
  source_ids: number[];
}

export interface VideoSourceItem {
  id: number;
  source_id: number | null;
  video_id: string;
  platform: string;
  url: string;
  title: string;
  channel_name: string | null;
  channel_url: string | null;
  language: string;
  research_language: string | null;
  duration_seconds: number | null;
  view_count: number | null;
  published_at: string | null;
  description: string | null;
  discovery_query: string | null;
  classification: string;
  source_independence: string;
  creator_source_family: string | null;
  duplicate_group: string | null;
  relevance_score: number | null;
  novel_information_ratio: number | null;
  value_score: number | null;
  transcript_status: string;
  transcript_type: string | null;
  segment_count: number;
  claim_count: number;
  processed_at: string | null;
}

export interface TranscriptClaimItem {
  id: number;
  canonical_claim_en: string;
  original_claim: string | null;
  original_language: string;
  claim_type: string;
  certainty: string;
  verification_status: string;
  timestamp_start: number | null;
  timestamp_end: number | null;
  speaker: string | null;
  quote_classification: string | null;
  confidence: number;
  cluster_id: number | null;
  promoted_fact_id: number | null;
  segment_ids: number[];
}

export interface NarrativeInsightItem {
  id: number;
  type: string;
  text_original: string | null;
  canonical_text_en: string;
  language: string;
  segment_ids: number[];
}

export interface TranscriptSegmentItem {
  id: number;
  index: number;
  start_seconds: number;
  end_seconds: number;
  text: string;
  canonical: string | null;
}

export interface VideoDetail extends VideoSourceItem {
  claims: TranscriptClaimItem[];
  insights: NarrativeInsightItem[];
  segments: TranscriptSegmentItem[];
}

export interface ClaimClusterItem {
  id: number;
  canonical_claim_en: string;
  claim_type: string;
  narrative_value: string | null;
  languages: string[];
  confidence: number;
  support_status: string;
  independent_source_family_count: number;
  member_count: number;
  promoted_fact_id: number | null;
  claims: (TranscriptClaimItem & {
    video_source_id: number;
    video: string | null;
    channel: string | null;
  })[];
}

export interface DossierSourceRef {
  kind: string;
  source_id?: number;
  title?: string | null;
  source_type?: string;
  language?: string;
  content_status?: string;
  claim_id?: number;
  video_source_id?: number;
  video_title?: string | null;
  channel?: string | null;
  timestamp_start?: number | null;
  timestamp_end?: number | null;
  timestamp_label?: string | null;
  segment_ids?: number[];
}

export interface DossierEntry {
  evidence_id: string;
  canonical_claim_en: string;
  original_claim?: string | null;
  category?: string;
  narrative_value?: string | null;
  evidence_strength?: string | null;
  quote_status?: string | null;
  speaker?: string | null;
  people?: string[];
  locations?: string[];
  event_date?: string | null;
  disputed?: boolean;
  confidence: number;
  status?: string;
  source_refs: DossierSourceRef[];
  independent_source_count?: number;
  languages?: string[];
}

export interface CaseDossier {
  case_id: number;
  case_identity: { title: string; summary: string | null; language: string };
  verified_facts: DossierEntry[];
  timeline: DossierEntry[];
  people: string[];
  locations: string[];
  human_details: DossierEntry[];
  scene_details: DossierEntry[];
  investigation_details: DossierEntry[];
  physical_evidence: DossierEntry[];
  legal_details: DossierEntry[];
  historical_context: DossierEntry[];
  quotes: DossierEntry[];
  contradictions: {
    evidence_id: string;
    topic: string;
    description: string;
    severity: string;
    source_refs: DossierSourceRef[];
  }[];
  disputed_claims: DossierEntry[];
  unverified_claims: {
    evidence_id: string;
    canonical_claim_en: string;
    claim_type: string;
    verification_status: string;
    video_title: string | null;
    timestamp_label: string | null;
  }[];
  theories: {
    evidence_id: string;
    canonical_claim_en: string;
    verification_status: string;
    video_title: string | null;
  }[];
  narrative_questions: {
    type: string;
    text: string;
    video_source_id: number | null;
  }[];
  research_gaps: unknown[];
  stats: {
    evidence_items: number;
    transcript_claims: number;
    claim_clusters: number;
    videos: number;
    insights: number;
  };
}

export type StoryStatus =
  | "draft"
  | "needs_revision"
  | "ready"
  | "outdated"
  | "failed";

export type StoryKind = "master" | "localized" | "direct" | "spoken";

export interface StoryMeta {
  id: number;
  case_id: number;
  version: number;
  narrative_angle: string;
  language: string;
  kind: StoryKind;
  master_version_id: number | null;
  derived_from_master_version: number | null;
  native_quality_score: number | null;
  semantic_consistency_score: number | null;
  factual_consistency_score: number | null;
  engagement_score: number;
  similarity_score: number | null;
  similarity_status: string;
  status: StoryStatus;
  is_best: boolean;
  word_count: number;
  dimensions: Record<string, number>;
  problems: string[];
  rewrite_instructions: string[];
  generation_provider: string | null;
  generation_model: string | null;
  text_hash: string | null;
  narrative_structure: {
    title?: string | null;
    central_question?: string | null;
    acts?: { id: string; title?: string; purpose?: string; word_target?: number }[];
    sections?: { id: string; words: number }[];
  } | null;
  created_at: string;
}

export interface StoryFull extends StoryMeta {
  story_text: string;
}

export interface AgentRun {
  id: number;
  case_id: number | null;
  case_title?: string | null;
  agent_name: string;
  status: RunStatus;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  input_summary: string | null;
  output_summary: string | null;
  error: string | null;
  provider: string | null;
  model: string | null;
  role: string | null;
  temperature: number | null;
  fallback_used: boolean;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  estimated_cost_usd: number | null;
  generation_id: string | null;
  text_hash: string | null;
}

/**
 * suggested: shown to the user · accepted: became a case · ignored: the
 * user said no · duplicate: the same real-world case exists already ·
 * filtered: UNSOLVED while the run did not include unsolved cases.
 */
export type SuggestionState = "suggested" | "accepted" | "ignored" | "duplicate" | "filtered";

/** `candidate_dict` of app/lifecycle/selection.py — one stored suggestion. */
export interface SuggestionRecord {
  id: number;
  candidate_id: number;
  title: string;
  query: string;
  rationale: string;
  state: SuggestionState;
  selected: boolean;
  rejected: boolean;
  case_id: number | null;
  resolution_status: ResolutionStatus;
  resolution_confidence: number | null;
  /** The evidence for the status (verifier's latest development, or the claim). */
  resolution_evidence: string | null;
  incident_date: string | null;
  latest_development_date: string | null;
  location: string | null;
  aliases: string[];
  people: string[];
  source_urls: string[];
  recency_score: number | null;
  rank_score: number | null;
  /** Ranking, dates, status evidence and what it was checked against. */
  suggestion_reason: string | null;
  duplicate_of_case_id: number | null;
  duplicate_of_candidate_id: number | null;
  duplicate_reason: string | null;
  created_at: string | null;
}

/** What the `case_status_verifier` read from targeted searches. */
export interface StatusVerification {
  status?: ResolutionStatus;
  confidence?: number;
  solved_by?: string | null;
  latest_development?: string | null;
  latest_development_date?: string | null;
  incident_date?: string | null;
  key_facts?: ({ fact?: string; url?: string } | string)[];
  supporting_urls?: string[];
  reason?: string | null;
}

/**
 * A suggestion of a discovery run: the stored record plus what the
 * discovery extraction said about it. The fallback agent (no search
 * engine configured) returns only the legacy fields.
 */
export interface DiscoveryCandidate extends Partial<Omit<SuggestionRecord, "candidate_id" | "title" | "rationale">> {
  candidate_id: number;
  title: string;
  rationale: string;
  narrative_potential?: string | null;
  languages_available?: string[];
  source_richness?: string | null;
  angles?: string[];
  suggested_queries?: string[];
  already_covered?: boolean;
  matched_existing_title?: string | null;
  key_people?: string[];
  approximate_date?: string | null;
  verification?: StatusVerification | null;
}

export interface SelectionStats {
  duplicates?: number;
  filtered_unsolved?: number;
  verified?: number;
  status_searches?: number;
  checked_against?: { cases?: number; suggestions?: number };
}

export interface DiscoveryRequest {
  count: number;
  languages: string[];
  theme: string;
  prefer_undercovered: boolean;
  require_multiple_sources: boolean;
  search_web: boolean;
  search_youtube: boolean;
  avoid_existing: boolean;
  /** The standard pipeline suggests SOLVED cases; true also suggests UNSOLVED ones. */
  include_unsolved: boolean;
}

/** 409 detail when the duplicate checker matched an existing case. */
export interface DuplicateConflict {
  message: string;
  duplicate: boolean;
  matched_kind: "case" | "candidate" | "batch" | string | null;
  matched_id: number | null;
  matched_title: string | null;
  matched_state: string | null;
  score: number;
  reasons: string[];
  /** e.g. "same victim 'Inga Gehricke' + same place 'Stendal'". */
  reason: string;
}

export interface CreateCasePayload {
  canonical_title: string;
  language?: string;
  summary?: string;
  resolution_status?: ResolutionStatus;
  aliases?: string[];
  people?: string[];
  location?: string | null;
  incident_date?: string | null;
  /** Create even though the duplicate checker found the same case. */
  force?: boolean;
}

export interface CreatedCase extends Partial<ResolutionFields> {
  id: number;
  canonical_title: string;
  slug: string;
  /** Investigate: the suggestion had already become this case. */
  existing?: boolean;
}

// ---------------------------------------------------------------------------
// Case lifecycle — app/lifecycle (status history, monitor, films, follow-ups)
// ---------------------------------------------------------------------------

/** A source behind a status decision. */
export interface StatusSource {
  url: string;
  title?: string | null;
}

/** Who changed a status. */
export type StatusChanger = "discovery" | "verifier" | "monitor" | "research" | "user";

export interface StatusHistoryEntry {
  id: number;
  case_id: number;
  /** null for the very first status of a case. */
  previous_status: ResolutionStatus | null;
  new_status: ResolutionStatus;
  confidence: number | null;
  reason: string | null;
  sources: StatusSource[];
  changed_by: StatusChanger | string;
  status_check_id: number | null;
  created_at: string;
}

/** GET/PUT /api/cases/{id}/status */
export interface CaseResolution extends ResolutionFields {
  statuses: ResolutionStatus[];
  history: StatusHistoryEntry[];
}

export interface SetStatusPayload {
  status: ResolutionStatus;
  reason: string;
  sources?: string[];
  summary?: string | null;
}

export interface MonitorSignal {
  /** arrest, suspect_identified, conviction … */
  signal: string;
  term: string;
  url: string | null;
  title: string | null;
  snippet: string;
  published_at: string | null;
}

export type StatusCheckOutcome =
  | "no_signal"
  | "signal"
  | "confirmed_change"
  | "not_confirmed"
  | "unchanged"
  | "error";

/** One monitor check of one case (`check_dict`). */
export interface StatusCheck {
  id: number;
  case_id: number;
  monitor_run_id: number | null;
  /** fast: searches + signal words only · deep: pages fetched + the verifier. */
  stage: "fast" | "deep";
  outcome: StatusCheckOutcome | string;
  previous_status: ResolutionStatus | null;
  current_status: ResolutionStatus | null;
  confidence: number | null;
  queries: { query: string; language?: string; time_range?: string }[];
  signals: MonitorSignal[];
  sources: StatusSource[];
  new_facts: ({ fact?: string; url?: string } | string)[];
  reason: string | null;
  search_calls: number;
  fetch_calls: number;
  llm_calls: number;
  created_at: string;
}

export interface MonitorRun {
  id: number;
  trigger: "scheduled" | "manual" | string;
  status: "running" | "completed" | "failed" | string;
  started_at: string;
  finished_at: string | null;
  cases_checked: number;
  deep_checks: number;
  status_changes: number;
  follow_ups_created: number;
  search_calls: number;
  fetch_calls: number;
  llm_calls: number;
  error: string | null;
}

/** GET /api/monitor */
export interface MonitorStatus {
  enabled: boolean;
  running_in_process: boolean;
  interval_hours: number;
  last_run_at: string | null;
  next_run_at: string | null;
  /** UNSOLVED / STATUS_UNDER_REVIEW cases the monitor checks. */
  watched_cases: number;
  runs: MonitorRun[];
}

export type ProductionType = "original" | "follow_up";
export type FilmState = "rendered" | "published" | "archived";

/** `video_dict` of app/lifecycle/videos.py — one film in one language. */
export interface Film {
  id: number;
  case_id: number;
  production_script_id: number | null;
  job_id: number | null;
  language: string;
  mode: DocumentaryMode;
  production_type: ProductionType;
  /** Follow-ups: the earlier video this one updates. */
  original_video_id: number | null;
  episode_number: number | null;
  title: string | null;
  youtube_title: string | null;
  youtube_description: string | null;
  youtube_tags: string[];
  status_at_production: ResolutionStatus;
  status_at_publication: ResolutionStatus | null;
  opening_strategy: string | null;
  duration_seconds: number | null;
  state: FilmState;
  published_at: string | null;
  youtube_url: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
  /** Relative API path — prefix with API_BASE. */
  video_url: string | null;
}

/** GET /api/films */
export interface FilmListItem extends Film {
  case_title: string;
  case_status: ResolutionStatus;
}

export interface PublishFilmPayload {
  episode_number?: number | null;
  youtube_url?: string | null;
  published_at?: string | null;
}

export interface ArchiveCase extends ResolutionFields {
  id: number;
  title: string;
  status: CaseStatus;
  location: string | null;
  films: Film[];
}

export type ArchiveFilter = "ALL" | ResolutionStatus;

/** GET /api/archive — `counts` are of the cases in this response. */
export interface ArchiveResponse {
  filter: ArchiveFilter;
  counts: Record<ResolutionStatus, number>;
  cases: ArchiveCase[];
}

export type FollowUpState = "pending" | "approved" | "dismissed" | "in_production" | "produced";

/** A covered UNSOLVED case that is now SOLVED (`followups.candidate_dict`). */
export interface FollowUpCandidate {
  id: number;
  case_id: number;
  case_title: string | null;
  state: FollowUpState;
  previous_status: ResolutionStatus;
  new_status: ResolutionStatus;
  development: string | null;
  confidence: number | null;
  sources: StatusSource[];
  original_video: Film | null;
  status_check: { id: number; reason: string | null; created_at: string } | null;
  /** "Do you want to create an update video?" — asked by the API. */
  question: string;
  follow_up_job_id: number | null;
  follow_up_video_id: number | null;
  decided_at: string | null;
  created_at: string;
}

export interface ApproveFollowUpPayload {
  mode: DocumentaryMode;
  languages?: string[] | null;
  render_profile?: RenderProfile;
  pilot_seconds?: number | null;
}

/** One appearance of a picture in a production (MediaUsage). */
export interface AuditPicture {
  start: number | null;
  seconds: number | null;
  kind: string | null;
  tier: number | null;
  asset: string | number | null;
  title: string | null;
  sentence: string | null;
  reason: string | null;
  appearance: number;
  repeat_justified: boolean | null;
  repeat_reason: string | null;
}

/** One music cue or chosen silence (MusicUsage). */
export interface AuditMusicCue {
  start: number | null;
  end: number | null;
  purpose: string | null;
  mood: string | null;
  track: string | null;
  why: string | null;
  selection_reason: string | null;
}

/** A production-time visual search (visuals/gaps.py audit). */
export interface AuditSearchRequest {
  beat_id: string;
  from_sentence?: number | null;
  sentence?: string | null;
  entity?: string | null;
  entity_name?: string | null;
  why?: string | null;
  source?: string | null;
  queries: string[];
  assets_found: string[];
  used: boolean;
}

export interface AuditProduction {
  production_script_id: number;
  language: string;
  mode: DocumentaryMode;
  case_status: ResolutionStatus | null;
  production_type: ProductionType | null;
  opening_strategy: string | null;
  pictures: AuditPicture[];
  maps: { start: number | null; reason: string | null; sentence: string | null }[];
  music: AuditMusicCue[];
  search_requests: AuditSearchRequest[];
}

/** GET /api/cases/{id}/audit — why the system decided what it decided. */
export interface CaseAudit {
  case: { id: number; title: string; origin: CaseOrigin | null } & ResolutionFields;
  /** Suggestions that became this case, and duplicates that point to it. */
  suggestions: SuggestionRecord[];
  status_history: StatusHistoryEntry[];
  status_checks: StatusCheck[];
  productions: AuditProduction[];
  films: Film[];
  follow_ups: FollowUpCandidate[];
}

export interface SettingsStatus {
  generation: {
    provider: string;
    base_url?: string;
    configured: boolean;
    models: Record<string, string>;
    routing: Record<string, string>;
    fallbacks: Record<string, string[]>;
  };
  research?: {
    provider: string;
    models: Record<string, string>;
    routing: Record<string, string>;
  };
  search: { provider: string; configured: boolean };
  youtube: { configured: boolean };
  research_provider: { provider: string; configured: boolean };
  multilingual?: {
    research_languages: string[];
    canonical_language: string;
    localization_languages: string[];
  };
  database: { connected: boolean; url: string };
}

export interface ResearchProviderStatus {
  provider: string;
  configured: boolean;
  reachable: boolean;
}

// TrueCrime Search Engine — first-party search/fetch/index infrastructure.
export interface SearchEngineStatus {
  provider: string;
  engine: string;
  configured: boolean;
  reachable: boolean;
  status: string;
  search_backend?: {
    backend?: string;
    healthy?: boolean;
    status_code?: number;
    base_url?: string;
    reason?: string;
  };
  fetcher?: { configured?: boolean };
  embedding?: {
    configured?: boolean;
    model?: string | null;
    cache_hits?: number;
  };
  llm?: { provider?: string; configured?: boolean };
}

// APIMaster — generation/analysis scope only.
export interface ApimasterStatus {
  provider: string;
  configured: boolean;
  reachable: boolean;
  authorized: boolean | null;
  status: string;
  detail?: string;
  models_available: string[];
  models_missing: string[];
  models: Record<string, string>;
  routing: Record<string, string>;
  fallbacks: Record<string, string[]>;
}

export type JobStatus = "queued" | "running" | "completed" | "failed";

export interface ResearchJob {
  id: number;
  case_id: number | null;
  provider: string;
  external_job_id: string | null;
  job_type: "discovery" | "research";
  status: JobStatus;
  current_stage: string | null;
  profile: string | null;
  model: string | null;
  search_calls: number | null;
  fetch_calls: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  model_cost_usd: number | null;
  search_cost_usd: number | null;
  fetch_cost_usd: number | null;
  total_cost_usd: number | null;
  sources_discovered: number | null;
  sources_accepted: number | null;
  sources_rejected: number | null;
  languages_completed: string[] | null;
  error_code: string | null;
  duration_ms: number | null;
  result_summary: string | null;
  error: string | null;
  result?: {
    candidates?: DiscoveryCandidate[];
    /** Not suggested: duplicates and filtered (unsolved) candidates, with reasons. */
    rejected?: DiscoveryCandidate[];
    selection_stats?: SelectionStats;
    skipped_duplicates?: number;
    sources_added?: number;
    language_stats?: Record<string, LanguageRunStats>;
    stop_reason?: string;
    [key: string]: unknown;
  } | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface LanguageRunStats {
  queries?: number;
  search_calls?: number;
  fetch_calls?: number;
  sources_found?: number;
  sources_accepted?: number;
  unique_sources?: number;
  full_text?: number;
  partial_text?: number;
  summary_only?: number;
  metadata_only?: number;
  rejected?: number;
  cost_usd?: number;
  stop_reason?: string;
}

export interface ResearchResultItem {
  id: number;
  url: string;
  final_url: string | null;
  title: string | null;
  rank: number | null;
  model: string | null;
  result_language: string | null;
  relevance_score: number | null;
  credibility_score: number | null;
  novelty_score: number | null;
  fetch_status: string | null;
  accepted: boolean;
  rejection_reason: string | null;
  source_ids: number[];
}

export interface ResearchQueryItem {
  id: number;
  language: string;
  query: string;
  purpose: string | null;
  priority: string | null;
  round: number;
  created_at: string;
  results: ResearchResultItem[];
}

export interface DiscoveredByQuery {
  query: string;
  language: string;
  purpose: string | null;
  round: number;
  link_type: string;
}

export interface SourceDetail extends Source {
  discovered_by: DiscoveredByQuery[];
}

export interface JobStartResponse {
  job_id: number | null;
  status: JobStatus;
  result?: Record<string, unknown>;
}

export interface ResearchLanguageStats {
  searched: boolean;
  queries: string[];
  sources: number;
  full_text_sources: number;
  metadata_only: number;
  summary_only: number;
  partial_text: number;
  full_text: number;
  unavailable: number;
  chunks: number;
  source_families: number;
  evidence_items: number;
  unique_evidence: number;
  queries_run?: number | null;
  search_calls?: number | null;
  fetch_calls?: number | null;
  sources_found?: number | null;
  sources_accepted?: number | null;
  rejected?: number | null;
  cost_usd?: number | null;
  stop_reason?: string | null;
}

export interface ResearchLanguages {
  canonical_language: string;
  languages: Record<string, ResearchLanguageStats>;
  total_sources: number;
}

export interface NarrativeCapacity {
  requested_minutes: number;
  estimated_supported_minutes: number;
  minimum_required_minutes: number;
  confidence?: number;
  depth?: Record<string, number>;
  weak_areas?: string[];
  status: "ready" | "insufficient_for_requested_length";
}

export interface SourceReadiness {
  status: string; // ready | insufficient
  reasons: string[];
  source_depth_minutes: number;
  required_source_minutes: number;
  retrieved_sources: number;
  independent_source_families: number;
  by_status: Record<string, number>;
}

export interface EvidenceReadiness {
  status: string; // ready | incomplete | blocked_provider | failed
  detail?: string | null;
  extraction_failed: boolean;
  evidence_items: number;
  facts: number;
  contradictions: number;
  timeline_events: number;
  human_details: number;
  scene_details: number;
  quotes: number;
  facts_with_strength: number;
  facts_with_provenance_text: number;
  retrieved_sources: number;
}

export interface MasterReadiness {
  status: string; // ready | insufficient_research | incomplete_evidence
  // | provider_blocked | failed
  reason?: string | null;
}

export interface ResearchDepth {
  sources: number;
  by_status: Record<string, number>;
  independent_source_families: number;
  chunks: number;
  capacity: NarrativeCapacity;
  gap_plan: { type: string; priority: string; reason: string }[];
  source_readiness?: SourceReadiness;
  evidence_readiness?: EvidenceReadiness;
  master_readiness?: MasterReadiness;
}

export interface SourceChunk {
  id: number;
  chunk_index: number;
  text: string;
  token_count: number;
  section_title: string | null;
  page_or_location: string | null;
  language: string;
}

export interface CorpusHit {
  chunk_id: number;
  source_id: number;
  source_title: string;
  source_url: string | null;
  language: string | null;
  text: string;
  location: string | null;
  score: number;
  bm25_score: number;
  dense_score: number;
}

export interface CorpusSearchResponse {
  query: string;
  language: string | null;
  case_id: number;
  chunks_indexed: number;
  dense_enabled: boolean;
  hits: CorpusHit[];
}

export interface MasterStoryResponse {
  master: StoryFull;
  localizations: StoryMeta[];
}

export interface SemanticReport {
  semantic_consistency_score?: number;
  missing_information?: unknown[];
  added_information?: unknown[];
  meaning_changes?: unknown[];
  uncertainty_changes?: unknown[];
  name_date_number_errors?: unknown[];
}

export interface LocalizationCompare {
  localization: StoryMeta;
  localized_text: string;
  master_text: string | null;
  master_version: number | null;
  semantic_consistency: SemanticReport;
  native_quality: Record<string, unknown>;
}

export interface DbOverviewSection<T> {
  count: number;
  items: T[];
}

export interface DbOverview {
  cases: DbOverviewSection<{ id: number; title: string; status: string; created_at: string }>;
  sources: DbOverviewSection<{
    id: number;
    case_id: number;
    title: string;
    source_type: string;
    publisher: string | null;
    created_at: string;
  }>;
  facts: DbOverviewSection<{
    id: number;
    case_id: number;
    claim: string;
    category: string;
    confidence: number;
  }>;
  contradictions: DbOverviewSection<{
    id: number;
    case_id: number;
    topic: string;
    severity: string;
  }>;
  stories: DbOverviewSection<{
    id: number;
    case_id: number;
    version: number;
    engagement_score: number;
    created_at: string;
  }>;
  discovery_history: DbOverviewSection<{
    id: number;
    title: string;
    selected: boolean;
    rejected: boolean;
    created_at: string;
  }>;
  agent_runs: { count: number };
}

// ---------------------------------------------------------------------------
// Documentary production — mirrors app/documentary/api.py serializers.
// ---------------------------------------------------------------------------

export type DocumentaryMode = "pilot" | "full";
export type RenderProfile = "preview" | "publish";
export type DocumentaryJobStatus =
  | "queued"
  | "running"
  | "cancelling"
  | "completed"
  /** Finished, but at least one language failed (see `result.errors`). */
  | "partial"
  | "failed"
  | "cancelled"
  /** The app restarted while the job ran: saved stages are kept, Resume continues. */
  | "interrupted";
/** "blocked": never ran because its language failed earlier.
 * "degraded": finished without part of its work (reason in detail.degraded). */
export type DocumentaryStageStatus =
  | "pending"
  | "running"
  | "done"
  | "skipped"
  | "failed"
  | "blocked"
  | "degraded";

export interface DocumentaryStageError {
  at: string;
  attempt: number | null;
  type: string;
  message: string;
}

export interface FilmMinutesRange {
  min: number;
  max: number;
}

export interface VoiceStyleSettings {
  stability: number;
  similarity_boost: number;
  style: number;
  use_speaker_boost: boolean;
  speed: number;
}

/** Shared limits of what runs at the same time (config: concurrency). */
export interface ConcurrencyLimits {
  llm: number;
  vision: number;
  elevenlabs_tts: number;
  elevenlabs_sound: number;
  asr: number;
  render: number;
  downloads: number;
  /** Documentaries running at the same time; more wait in "queued". */
  jobs: number;
  /** Language branches of one documentary running at the same time. */
  languages: number;
}

/** GET /api/documentary/settings — never carries provider secrets. */
export interface DocumentarySettings {
  languages: string[];
  film_minutes: FilmMinutesRange;
  pilot_seconds: number;
  voices: Record<
    string,
    { voice_id: string | null; model_id: string; language_code: string | null; audio_tags: boolean }
  >;
  /** Listening check of words a voice can misread (Persian homographs). */
  pronunciation_check: {
    enabled: boolean;
    languages: string[];
    /** Corrected takes per block before a word is flagged for a person. */
    max_rounds: number;
  };
  voice_performance: {
    enabled: boolean;
    /** Allowed audio tags per arc level "0".."3". */
    level_tags: Record<string, string[]>;
    /** Voice style per arc level. */
    level_styles: Record<string, string>;
  };
  concurrency: ConcurrencyLimits;
  styles: Record<string, VoiceStyleSettings>;
  render: {
    profile: string;
    width: number;
    height: number;
    fps: number;
    crf: number;
    preset: string;
    burn_subtitles: boolean;
    subtitle_max_chars: number;
    subtitle_max_seconds: number;
    show_credits: boolean;
  };
  rights_profiles: Record<string, string[]>;
  dynamic_eq: DynamicEQSettings;
  channels?: Record<string, { name: string; studio_dir?: string | null; studio_profile?: string | null }>;
  /** Avatar videos cost provider credits: generated only when enabled in config. */
  avatar_generation?: { enabled: boolean; output_format: string };
}

export interface DocumentaryStage {
  /** e.g. "blueprint", "spoken:fa", "render:en" */
  name: string;
  status: DocumentaryStageStatus;
  /** Stage result (object) or the failure message (string). */
  detail: unknown;
  /** How often the stage was started (resumes and retries included). */
  attempts?: number;
  started_at?: string;
  finished_at?: string;
  /** The last failures (newest last); kept when a later attempt succeeds. */
  errors?: DocumentaryStageError[];
}

export interface DocumentaryJob {
  id: number;
  case_id: number;
  /** null until a from-zero job has written its master story. */
  master_version_id: number | null;
  mode: DocumentaryMode;
  languages: string[];
  pilot_seconds: number | null;
  render_profile: RenderProfile;
  /** Re-run visual research, verification and shot direction. */
  refresh_visuals: boolean;
  /** Researches the case and writes the master story first. */
  from_zero: boolean;
  /** Length the master story is written for (from-zero jobs). */
  target_minutes: number | null;
  batch_id: string | null;
  /** "follow_up": an update video about a case covered before (approved follow-up). */
  production_type: ProductionType;
  follow_up_id: number | null;
  status: DocumentaryJobStatus;
  /** Running stage names, comma-separated (stages run in parallel); may be truncated. */
  stage: string | null;
  /** 0–1: share of stages done or skipped. */
  progress: number;
  stages: DocumentaryStage[];
  /** `errors`: failure per language of a "partial" job; `renders`: the film per language. */
  result: {
    errors?: Record<string, string>;
    /** Stages that finished without part of their work, with the reason. */
    degraded?: Record<string, string>;
    renders?: Record<string, JobRender>;
    [key: string]: unknown;
  };
  error: string | null;
  created_at: string;
  updated_at: string | null;
  completed_at: string | null;
}

/** A language's render in a job result: the MP4 and the Video record made from it. */
export interface JobRender {
  production_script_id: number;
  video_id?: number;
  youtube_title?: string | null;
  [key: string]: unknown;
}

/** GET /api/documentary/jobs — jobs of every case. */
export interface DocumentaryJobListItem extends DocumentaryJob {
  case_title: string | null;
}

/** Options shared by a single job and a batch. */
export interface DocumentaryRunOptions {
  languages: string[];
  mode: DocumentaryMode;
  pilot_seconds: number | null;
  render_profile: RenderProfile;
  refresh_visuals: boolean;
}

export interface DocumentaryJobRequest extends DocumentaryRunOptions {
  master_version_id: number | null;
  from_zero: boolean;
  /** 45–120 min: the master story's length when starting from zero. */
  target_minutes: number | null;
}

export interface DocumentaryBatchItem {
  case_id: number;
  from_zero: boolean;
  target_minutes: number | null;
}

export interface DocumentaryBatchRequest extends DocumentaryRunOptions {
  items: DocumentaryBatchItem[];
}

export interface DocumentaryBatchStart {
  batch_id: string;
  jobs: DocumentaryJob[];
  rejected: { case_id: number; reason: string }[];
  max_parallel_jobs: number;
}

export interface DocumentaryBatch {
  batch_id: string;
  jobs: DocumentaryJob[];
  /** 0–1: mean progress of the batch's jobs. */
  progress: number;
  statuses: Record<string, number>;
}

/** GET /api/documentary/scheduler — what runs now and what waits. */
export interface DocumentaryScheduler {
  max_parallel_jobs: number;
  running: DocumentaryJob[];
  queued: DocumentaryJob[];
  limits: Record<string, { active: number; limit: number }>;
}

export type Level = "low" | "medium" | "high";

export interface ValidationIssue {
  code: string;
  [key: string]: unknown;
}

export interface BlueprintBeat {
  id: string;
  act_id: string;
  paragraphs: [number, number];
  summary: string;
  human_focus: string | null;
  purpose: string;
  emotional_load: Level;
  information_density: Level;
  mystery_intensity: Level;
  attention: string;
  visual_intent: string;
  audio_intent: string;
  pause_after: string;
  music_intent: string;
  words: number;
  reveals: string[];
  relies_on: string[];
  opens: string[];
  answers: string[];
  unresolved: string[];
  viewer_knows?: string[];
  open_questions?: string[];
}

export interface BlueprintQuestion {
  id: string;
  question: string;
  kind: "mystery" | "human" | "investigation";
  opened_in: string | null;
  resolved_in: string | null;
  status: "answered" | "unresolved" | "open" | "never_opened";
}

export interface BlueprintRecord {
  id: number;
  case_id: number;
  story_version_id: number;
  version: number;
  status: "valid" | "needs_review" | "invalid";
  evidence_fingerprint: string | null;
  generation_model: string | null;
  created_at: string;
  blueprint: {
    central_question?: string;
    editorial_thesis?: string;
    human_thread?: string;
    arcs?: Record<string, string>;
    questions?: BlueprintQuestion[];
    beats?: BlueprintBeat[];
  };
  validation: {
    status?: string;
    errors?: ValidationIssue[];
    warnings?: ValidationIssue[];
    unanswered_questions?: string[];
  };
}

export interface AudioPlanBeat {
  beat_id: string;
  purpose: string | null;
  paragraph_breath: string;
  bed: string;
  bed_level: string;
  after: { type: string; seconds: number | null; mood: string | null };
  why: string;
}

export interface AudioPlanRecord {
  id: number;
  case_id: number;
  blueprint_id: number;
  version: number;
  status: string;
  generation_model: string | null;
  created_at: string;
  plan: { notes?: string; beats?: AudioPlanBeat[] };
  validation: {
    status?: string;
    estimated_runtime_seconds?: number;
    music_only_seconds?: number;
    music_only_share?: number;
    music_moments?: number;
    bed_switches?: number;
  };
}

export interface CritiqueIssue {
  check: string;
  severity: "low" | "medium" | "high";
  why: string;
  shot?: number;
  time?: string;
  fix?: string;
}

export interface DeterministicReport {
  issues: CritiqueIssue[];
  changes_per_minute: number;
  music_only_share: number;
  high: number;
}

export interface CriticProblem {
  time?: string;
  shot?: number;
  beat_id?: string;
  severity?: string;
  why?: string;
  fix?: string;
  fix_detail?: string;
}

export interface CriticReport {
  score: number | null;
  summary: string | null;
  problems: CriticProblem[];
  model: string | null;
}

export interface AppliedFix {
  shot: number;
  fix: string;
  to?: string;
}

export interface CritiqueReport {
  deterministic: DeterministicReport;
  critics: Record<string, CriticReport>;
  fixes: AppliedFix[];
  after_fixes?: DeterministicReport;
  score: number | null;
}

export interface RenderInfo {
  path: string;
  srt: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  frames: number;
  shots: number;
}

export interface ScriptShot {
  index: number;
  beat_id: string;
  start: number;
  end: number;
  command: string;
  kind?: string;
  motion: string;
  speed?: number;
  transition_in: string;
  asset_id?: string | null;
  type?: string;
  role?: string;
  rights?: string;
  credit?: string | null;
  subject_type?: string | null;
  reframe?: boolean;
}

export interface ScriptOverlay {
  kind: string;
  text: string;
  start: number;
  end: number;
}

export interface ScriptMusic {
  role: string;
  cue_id: string | null;
  mood: string | null;
  start: number;
  duration: number;
  level_db: number | null;
  /** Library track (several variants per kind and mood). */
  track_code?: string | null;
  /** The audio director's reason for music (or silence) here. */
  why?: string | null;
  /** Why this track: unused in recent films, the film's theme … */
  selection_reason?: string | null;
}

export interface ScriptSilence {
  start: number;
  duration: number;
  kind: string | null;
}

/** Render-ready timeline of one language (app/documentary/production/script.py). */
export interface ProductionScriptData {
  language?: string;
  /** The case's resolution status when the script was composed (UNSOLVED films carry a status card). */
  case_status?: ResolutionStatus | null;
  production_type?: ProductionType;
  /** How the film opens (critical_moment, victim_introduction, previous_coverage …). */
  opening_strategy?: string | null;
  duration?: number;
  width?: number;
  height?: number;
  fps?: number;
  beats?: { beat_id: string; start: number; end: number }[];
  voice?: { block_id: string; start: number; end: number }[];
  silences?: ScriptSilence[];
  shots?: ScriptShot[];
  overlays?: ScriptOverlay[];
  music?: ScriptMusic[];
  subtitles?: { start: number; end: number; text: string }[];
  credits?: string[];
}

export interface VisualAuditReport {
  status: string;
  audited: number;
  approved: number;
  max_redos: number;
  replaced: { at: number; from: string; to: string; why: string[] }[];
  left_out: { at: number; rejected: string[]; why: string[]; done: string; sentences: string[] }[];
  symbolic: string[];
}

export interface ProductionSummary {
  id: number;
  story_version_id: number;
  language: string;
  version: number;
  mode: DocumentaryMode;
  status: string;
  duration_seconds: number | null;
  critique: CritiqueReport | null;
  /** The picture auditor's report (gate before render). */
  audit?: VisualAuditReport | null;
  render: RenderInfo | null;
  /** Relative API paths — prefix with API_BASE. */
  video_url: string | null;
  subtitles_url: string | null;
  created_at: string;
}

export interface Production extends ProductionSummary {
  script: ProductionScriptData;
}

export interface VoicePerformanceStats {
  sentences: number;
  directed: number;
  levels: { neutral: number; unease: number; dark: number; climax: number };
  /** Directed sentences carrying at least one audio tag. */
  tagged: number;
  tags: Record<string, number>;
  /** Validator findings by kind. */
  issues: Record<string, number>;
  /** Tags removed to keep each level's tag density. */
  thinned: number;
}

export interface DocumentaryLanguage {
  version_id: number;
  status: string;
  voice_performance: { id: number; status: string; stats: VoicePerformanceStats | null } | null;
  quality_gates: { pass: boolean; failures: string[] } | null;
  storyteller_beats: number | null;
  beats: number | null;
  estimated_film_minutes: number;
  production: ProductionSummary | null;
}

export interface DocumentaryMaster {
  id: number;
  version: number;
  language: string;
  status: string;
  words: number;
  kind: StoryKind;
}

export interface DocumentaryOverview {
  masters: DocumentaryMaster[];
  master_version_id: number | null;
  film_minutes: FilmMinutesRange;
  blueprint: BlueprintRecord | null;
  audio_plan: AudioPlanRecord | null;
  languages: Record<string, DocumentaryLanguage | null>;
  visual_plan: {
    id: number;
    status: string;
    version: number;
    validation: Record<string, unknown>;
  } | null;
  jobs: DocumentaryJob[];
  /** Visual assets of the case by verification status. */
  visual_counts: Record<string, number>;
}

export type AssetRole = "evidence" | "context" | "illustration";
export type VerificationStatus = "verified" | "needs_review" | "rejected" | "unverified";

export interface VisualAsset {
  id: number;
  asset_id: string;
  type: string;
  subject_type: string | null;
  title: string | null;
  description: string | null;
  caption: string | null;
  entities: string[];
  role: AssetRole;
  provider: string;
  source_url: string | null;
  page_url: string | null;
  source_name: string | null;
  found_for: string | null;
  license: string | null;
  credit: string | null;
  rights: string;
  rights_reason: string | null;
  usable_preview: boolean;
  usable_publish: boolean;
  verification: VerificationStatus;
  verification_confidence: number | null;
  verification_detail: Record<string, unknown> | null;
  quality: number | null;
  reveals: string[];
  width: number | null;
  height: number | null;
  date: string | null;
  location: string | null;
  human_override: boolean;
  /** Relative API paths — prefix with API_BASE. */
  image_url: string;
  thumbnail_url: string;
  created_at: string;
}

export interface VisualFilters {
  role?: string;
  rights?: string;
  verification?: string;
  q?: string;
}

export interface VisualUpdate {
  role?: AssetRole;
  rights?: string;
  verification?: VerificationStatus;
}

export interface VisualUpload {
  file: File;
  title: string;
  caption: string;
  role: AssetRole;
  rights: string;
  /** Video only: second of the file where the kept part starts (max. 10 min are kept). */
  start?: number;
}

/** GET /api/documentary/music — one track of the library with its usage (`track_catalogue`). */
export interface MusicTrack {
  /** The track code (also used in the file URL). */
  id: string;
  track_code: string;
  /** Config cue a variant-1 track was imported from. */
  cue_id: string | null;
  kind: string;
  mood: string;
  variant: number;
  style: string | null;
  seconds: number;
  loop: boolean;
  prompt: string;
  active: boolean;
  characters_paid: number | null;
  generated: boolean;
  /** Relative API path — prefix with API_BASE. */
  url: string;
  /** Placements across all films. */
  usage_count: number;
  films_count: number;
  /** Film keys (one film across its languages), in order of first use. */
  films: string[];
  last_used_at: string | null;
  created_at: string;
}

/** How a fix makes the voice say a word as its meaning needs. */
export type PronunciationFixKind = "vowelled" | "full" | "respell" | "synonym";

/** One risky word judged in a take: its vowels heard vs. the reading the key asks for. */
export interface PronunciationWord {
  word: string;
  /** Reading the meaning needs, in Latin letters (e.g. "molk"). */
  read: string;
  /** Form sent to the voice (the word itself until a fix applies). */
  form: string;
  /** Fixes applied so far (0 = none). */
  level: number;
  /** null: not located in the audio or nothing heard. */
  ok: boolean | null;
  expected: string[];
  heard: string[];
  heard_ipa: string | null;
  distance: number | null;
  reason: string | null;
  at: [number, number] | null;
}

export interface PronunciationFix {
  word: string;
  read: string;
  form: string;
  fix: PronunciationFixKind;
  level: number;
  sentence: number;
}

export interface BlockPronunciation {
  words: PronunciationWord[];
  /** Takes of the block (first take plus corrected ones). */
  rounds: number;
  /** `after_round`: 0-based take after which the fixes were applied. */
  fixes: { after_round: number; fixes: PronunciationFix[] }[];
  /** Words still said wrong after the last take — for a person to check. */
  unresolved: string[];
  per_round: { round: number | null; wrong: string[] }[];
}

export interface VoiceManifestBlock {
  block_id: string;
  section_id: string;
  words: number;
  est_seconds: number;
  actual_seconds: number;
  words_per_minute: number | null;
  attempts: number;
  cache_hit: boolean;
  characters_paid: number;
  style: string;
  beat_ids: string[];
  loudness_lufs: number | null;
  loudness_deviation_lu: number | null;
  asr: {
    passed: boolean;
    failures: string[];
    word_error_rate: number;
    heard_text?: string;
  } | null;
  flags: string[];
  /** Text actually sent to the voice (audio tags, added harakat) when it differs. */
  tts_text?: string | null;
  pronunciation?: BlockPronunciation | null;
}

/** Dynamic-EQ report inside the narration manifest (stats only — the
 * full gain-reduction curve is in dynamics.json via voiceDynamics). */
export interface DynamicEQBandReport {
  name: string;
  kind: "harsh" | "deesser";
  center_hz: number;
  baseline_db?: number;
  threshold_db?: number;
  max_gr_db?: number;
  mean_gr_db?: number;
  active_seconds: number;
  pct_active?: number;
}

export interface DynamicEQReport {
  enabled: boolean;
  applied: boolean;
  active?: "original" | "enhanced";
  cached?: boolean;
  error?: string;
  language?: string;
  active_seconds_reduced?: number;
  bands?: DynamicEQBandReport[];
  deesser?: DynamicEQBandReport | null;
}

/** GET .../voice/dynamics — the full sidecar report with curves+events. */
export interface VoiceDynamics {
  engine: string;
  language: string;
  duration_seconds: number;
  active_seconds_reduced: number;
  bands: (DynamicEQBandReport & {
    events: { start: number; end: number; max_gr_db: number }[];
    curve: { t: number; gr_db: number }[];
  })[];
  deesser:
    | (DynamicEQBandReport & {
        events: { start: number; end: number; max_gr_db: number }[];
        curve: { t: number; gr_db: number }[];
      })
    | null;
}

export interface DynamicEQSettings {
  enabled: boolean;
  strength: number;
  attack_ms: number;
  release_ms: number;
  bands: {
    name: string;
    center_hz: number;
    max_atten_db: number;
    threshold_offset_db: number;
  }[];
  deesser: {
    enabled: boolean;
    strength: number;
    center_hz: number;
    max_atten_db: number;
  };
  languages: string[];
}

export interface EQPreviewResponse {
  mp3_url: string;
  report: {
    cached: boolean;
    preview_seconds: number;
    active_seconds_reduced: number;
    bands: DynamicEQReport["bands"];
    deesser: DynamicEQReport["deesser"];
  };
}

/** GET /api/cases/{id}/stories/{version}/voice — narration manifest. */
export interface VoiceManifest {
  case_id: number;
  story_version_id: number;
  language: string;
  voice_id: string | null;
  model_id: string;
  styles_used: string[];
  provider: string;
  asr: string | null;
  asr_error: string | null;
  /** Phoneme model of the pronunciation check, or null when it did not run. */
  pronunciation_listener?: string | null;
  pronunciation_error?: string | null;
  blocks_rendered: number;
  blocks_in_plan: number;
  duration_seconds: number;
  estimated_seconds: number;
  characters_paid: number;
  loudness: {
    target_lufs: number;
    before_lufs: number | null;
    after_lufs: number | null;
    after_true_peak_db: number | null;
  };
  flags: string[];
  blocks: VoiceManifestBlock[];
  dynamic_eq?: DynamicEQReport;
  files?: { narration_original_mp3?: string };
  mix?: {
    music_moments: number;
    beds: number;
    music_only_seconds: number;
    loudness_lufs: number | null;
  };
}

/** One sentence: what the narrator says and what people read (subtitles). */
export interface SpeechSentence {
  speech: string;
  display: string | null;
}

/** Pronunciation key entry: a word a voice could misread, with the reading its meaning needs. */
export interface RiskyWord {
  w: string;
  /** Latin reading, e.g. "molk". */
  read: string;
  meaning: string;
  /** Minimal harakat (مُلک). */
  vowelled?: string;
  /** Full harakat (مُلْک). */
  full?: string;
  /** The same word spelled unambiguously. */
  respell?: string;
  synonym?: string;
  synonym_read?: string;
}

/** A sentence of the voice performance. */
export interface PerformanceSentence extends SpeechSentence {
  /** Text sent to the voice: [audio tags] and punctuation for timing. */
  tts: string | null;
  /** Arc level: 0 neutral · 1 unease · 2 dark · 3 climax. */
  level: number;
  directed: boolean;
  /** Pronunciation key of the sentence (languages with the check). */
  risky?: RiskyWord[] | null;
}

export interface PerformanceBeat {
  beat_id: string;
  arc_level: number;
  arc_peak: number;
  paragraphs: PerformanceSentence[][];
}

/** GET/POST /api/documentary/versions/{id}/voice-performance */
export interface VoicePerformance {
  id: number;
  story_version_id: number;
  language: string;
  version: number;
  status: string;
  model: string | null;
  performance: {
    language: string;
    /** Beat of the story's first incident (everything before it stays neutral). */
    incident_beat: string | null;
    incident_sentence: number | null;
    stats: VoicePerformanceStats;
    beats: PerformanceBeat[];
  };
  validation: {
    arc_log: string[];
    level_log: string[];
    errors: string[];
    examples: { i: number; issue: string; raw: string | null }[];
    pronunciation_key?: {
      sentences: number;
      risky_words: number;
      issues: string[];
      errors: string[];
    } | null;
  };
  created_at: string;
}

/** GET /api/documentary/versions/{id}/speech */
export interface SpeechStructure {
  version_id: number;
  language: string;
  beats: { beat_id: string; paragraphs: SpeechSentence[][] }[];
}

// ---------------------------------------------------------------------------
// Channel studios + host scenes
// ---------------------------------------------------------------------------

export interface StudioZone {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface StudioAssetView {
  id: string;
  language: string;
  file_name: string;
  camera: number | null;
  shot: string;
  asset_type: string;
  camera_angle: string;
  shot_size: string;
  width: number | null;
  height: number | null;
  aspect_ratio: number | null;
  orientation: string | null;
  approved_for_host: boolean;
  approved_for_avatar: boolean;
  lighting_style: string | null;
  studio_style: string | null;
  notes: string | null;
  safe_zones: {
    host?: StudioZone;
    head?: StudioZone;
    logo?: StudioZone;
    lower_third?: StudioZone;
    host_standing?: StudioZone;
    head_standing?: StudioZone;
  };
  file_present: boolean;
  image_url: string;
}

export interface FramingPreset {
  asset_id: string;
  crop?: StudioZone | null;
  background_blur: number;
  host_height_ratio: number;
  host_center_x: number;
  host_bottom: number;
  camera_safe: StudioZone;
  subtitle_safe: StudioZone;
}

export interface StudioProfile {
  id: string;
  language: string;
  primary_background: string;
  presets: Record<string, FramingPreset>;
  alternate_angles: string[];
  background_mode: string;
  review: { by?: string | null; confirmed?: boolean; confirmed_by?: string };
}

export interface StudioValidation {
  language: string;
  ok: boolean;
  errors: string[];
  warnings: string[];
}

export interface StudioChannel {
  language: string;
  channel_name: string;
  elevenlabs_voice_id: string | null;
  studio_profile_id: string;
  heygen_avatar_env: string;
  heygen_key_env: string;
  profile: StudioProfile | null;
  assets: StudioAssetView[];
  validation: StudioValidation | null;
  upscale: Record<string, number | null>;
}

export interface StudiosResponse {
  channels: StudioChannel[];
  presets: string[];
  background_modes: string[];
}

export interface StudioPatch {
  primary_background?: string;
  presets?: Record<string, string>;
  confirm?: boolean;
}

export interface HostScene {
  id: number;
  case_id: number;
  story_version_id: number;
  host_segments_id: number;
  host_segment_id: string;
  language: string;
  channel: string;
  position: string | null;
  beat_id: string | null;
  text: string;
  text_sha256: string;
  studio_profile_id: string;
  studio_asset_id: string;
  framing_preset: string;
  host_position: { center_x?: number; bottom?: number };
  host_scale: number | null;
  background_mode: string;
  planned_start: number | null;
  planned_duration: number | null;
  voice_id: string | null;
  voice_ready: boolean;
  voice_seconds: number | null;
  voice_sha256: string | null;
  avatar_provider: string | null;
  avatar_id: string | null;
  provider_job_id: string | null;
  provider_generation: number;
  avatar_ready: boolean;
  avatar_video_sha256: string | null;
  status: string;
  failed_step: string | null;
  last_error: string | null;
  attempts: Record<string, number>;
  history: { at: string; step: string; outcome: string; detail?: unknown }[];
  running: boolean;
  created_at: string;
  updated_at: string;
}
