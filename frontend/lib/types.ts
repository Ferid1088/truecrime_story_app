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

export interface CaseListItem {
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

export interface CaseDetail {
  id: number;
  title: string;
  slug: string;
  status: CaseStatus;
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

export interface DiscoveryCandidate {
  candidate_id: number;
  title: string;
  rationale: string;
  narrative_potential: string;
  languages_available: string[];
  source_richness: string;
  angles: string[];
  suggested_queries?: string[];
  already_covered: boolean;
  matched_existing_title?: string | null;
  key_people?: string[];
  location?: string | null;
  approximate_date?: string | null;
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
  | "failed"
  | "cancelled";
export type DocumentaryStageStatus = "pending" | "running" | "done" | "skipped" | "failed";

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

/** GET /api/documentary/settings — never carries provider secrets. */
export interface DocumentarySettings {
  languages: string[];
  film_minutes: FilmMinutesRange;
  pilot_seconds: number;
  voices: Record<string, { voice_id: string | null; model_id: string }>;
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
}

export interface DocumentaryStage {
  /** e.g. "blueprint", "spoken:fa", "render:en" */
  name: string;
  status: DocumentaryStageStatus;
  /** Stage result (object) or the failure message (string). */
  detail: unknown;
}

export interface DocumentaryJob {
  id: number;
  case_id: number;
  master_version_id: number;
  mode: DocumentaryMode;
  languages: string[];
  pilot_seconds: number | null;
  render_profile: RenderProfile;
  /** Re-run visual research, verification and shot direction. */
  refresh_visuals: boolean;
  status: DocumentaryJobStatus;
  stage: string | null;
  /** 0–1: share of stages done or skipped. */
  progress: number;
  stages: DocumentaryStage[];
  result: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string | null;
  completed_at: string | null;
}

export interface DocumentaryJobRequest {
  master_version_id: number | null;
  languages: string[];
  mode: DocumentaryMode;
  pilot_seconds: number | null;
  render_profile: RenderProfile;
  refresh_visuals: boolean;
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
}

export interface ScriptSilence {
  start: number;
  duration: number;
  kind: string | null;
}

/** Render-ready timeline of one language (app/documentary/production/script.py). */
export interface ProductionScriptData {
  language?: string;
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

export interface ProductionSummary {
  id: number;
  story_version_id: number;
  language: string;
  version: number;
  mode: DocumentaryMode;
  status: string;
  duration_seconds: number | null;
  critique: CritiqueReport | null;
  render: RenderInfo | null;
  /** Relative API paths — prefix with API_BASE. */
  video_url: string | null;
  subtitles_url: string | null;
  created_at: string;
}

export interface Production extends ProductionSummary {
  script: ProductionScriptData;
}

export interface DocumentaryLanguage {
  version_id: number;
  status: string;
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
}

export interface MusicCue {
  id: string;
  kind: string;
  mood: string;
  seconds: number;
  loop: boolean;
  prompt: string;
  generated: boolean;
  /** Relative API path — prefix with API_BASE. */
  url: string;
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
  mix?: {
    music_moments: number;
    beds: number;
    music_only_seconds: number;
    loudness_lufs: number | null;
  };
}
