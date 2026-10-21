import type { Page } from "@playwright/test";
import { join } from "node:path";

/**
 * Deterministic API fixtures. Route interception happens at the browser
 * level, so requests to http://127.0.0.1:8000/api/* are fully mocked —
 * no backend required.
 */

const CASE = {
  id: 1,
  title: "The Lighthouse Keeper Vanishing",
  slug: "lighthouse-keeper",
  status: "story_ready",
  language: "en",
  summary: "A keeper vanishes from a remote lighthouse in 1900.",
  created_at: "2024-06-01T10:00:00Z",
  last_activity: "2024-06-02T10:00:00Z",
  archived: false,
  archive_reason: null,
  languages_found: ["en", "fa"],
  narrative_angle: null,
  sources: 3,
  facts: 4,
  contradictions: 1,
  story_versions: 2,
  latest_story_version: 2,
  engagement_score: 38,
  generation_usage: {
    input_tokens: 12000,
    output_tokens: 8000,
    total_tokens: 20000,
    estimated_cost_usd: 0.42,
  },
};
const SHORT_101_VIDEO = join(process.cwd(), "e2e/assets/short-101.mp4");
const SHORT_101_COVER = join(process.cwd(), "e2e/assets/short-101.png");

export const fixtures = {
  dashboard: {
    stats: {
      total_cases: 1,
      cases_researched: 1,
      stories_completed: 2,
      cases_waiting: 0,
      sources_collected: 3,
      facts_extracted: 4,
      contradictions_found: 1,
    },
    recent_cases: [CASE],
    agent_activity: [
      { agent_name: "Writer", status: "completed", started_at: "2024-06-02T09:00:00Z", duration_ms: 42000 },
      { agent_name: "Engagement Critic", status: "completed", started_at: "2024-06-02T09:05:00Z", duration_ms: 12000 },
      { agent_name: "Consistency Checker", status: "running", started_at: "2024-06-02T09:06:00Z", duration_ms: null },
    ],
  },
  cases: [CASE],
  caseDetail: CASE,
  sources: [
    {
      id: 11,
      case_id: 1,
      title: "Keepers' log reprint",
      url: "https://example.com/log",
      source_type: "article",
      language: "en",
      publisher: "Maritime Gazette",
      raw_text: "Full article text…",
      notes: null,
      reliability_score: 0.8,
      is_authorized_text: true,
      summary: "English summary of the log reprint.",
      summary_en: "English summary of the log reprint.",
      content_status: "full_text",
      value_flags: ["primary_source"],
      raw_text_length: 1200,
      published_at: "1990-01-01",
      retrieved_at: "2024-06-01T10:00:00Z",
      research_provider: "truecrime",
      external_reference: null,
      status: "active",
      created_at: "2024-06-01T10:00:00Z",
    },
    {
      id: 12,
      case_id: 1,
      title: "گزارش محلی فارسی",
      url: "https://example.com/fa",
      source_type: "news",
      language: "fa",
      publisher: "روزنامه محلی",
      raw_text: null,
      notes: null,
      reliability_score: 0.6,
      is_authorized_text: false,
      summary: "خلاصه فارسی گزارش.",
      summary_en: "Persian report summary translated.",
      content_status: "summary_only",
      value_flags: [],
      raw_text_length: 0,
      published_at: null,
      retrieved_at: "2024-06-01T10:05:00Z",
      research_provider: "truecrime",
      external_reference: null,
      status: "active",
      created_at: "2024-06-01T10:05:00Z",
    },
  ],
  facts: [
    {
      id: 21,
      case_id: 1,
      claim: "The keeper was last seen on December 15, 1900.",
      original_claim: null,
      original_language: "en",
      narrative_value: "turning_point",
      category: "timeline",
      confidence: 0.9,
      disputed: false,
      event_date: "1900-12-15",
      source_ids: [11],
    },
    {
      id: 22,
      case_id: 1,
      claim: "Local reports describe a storm that night.",
      original_claim: "گزارش‌های محلی از توفان آن شب خبر می‌دهند.",
      original_language: "fa",
      narrative_value: "atmospheric",
      category: "context",
      confidence: 0.55,
      disputed: true,
      event_date: "1900-12",
      source_ids: [12],
    },
  ],
  contradictions: [
    {
      id: 31,
      case_id: 1,
      topic: "Weather on the night of the disappearance",
      description: "English sources describe calm seas; Persian reports describe a storm.",
      severity: "medium",
      source_ids: [11, 12],
    },
  ],
  stories: [
    {
      id: 5,
      case_id: 1,
      version: 2,
      narrative_angle: JSON.stringify({
        title: "Three Men and a Locked Door",
        central_question: "What happened inside the lighthouse that night?",
      }),
      language: "en",
      kind: "final",
      master_version_id: null,
      draft_version_id: 9,
      reasoning_effort: "high",
      minutes: 62.4,
      cost_eur: 0.61,
      native_quality_score: null,
      semantic_consistency_score: null,
      factual_consistency_score: null,
      engagement_score: 38,
      similarity_score: 0.1,
      similarity_status: "ok",
      status: "ready",
      is_best: true,
      word_count: 2400,
      dimensions: { hook: 40, pacing: 35 },
      problems: [],
      rewrite_instructions: [],
      generation_provider: "apimaster",
      generation_model: "google/gemini-3.8-flash",
      text_hash: "abc123",
      narrative_structure: {
        title: "Three Men and a Locked Door",
        central_question: "What happened inside the lighthouse that night?",
        acts: [{ id: "act1", title: "Arrival", purpose: "Setup", word_target: 800 }],
        sections: [{ id: "s1", words: 2400 }],
        evidence_usage: { facts_used: 3, facts_total: 4 },
      },
      created_at: "2024-06-02T09:00:00Z",
      story_text: "The boat scraped against the rocks of Eilean Mòr.\n\nInside, the lighthouse stood silent.",
    },
  ],
  languagePipeline: {
    draft: { id: 9, stage: "draft", language: "en", status: "ready", model: "claude-sonnet-5-5", provider: "apimaster", reasoning_effort: "low", minutes: 91.2, words: 13680, cost_eur: 1.52, prompt: "language_pipeline/draft_writer", prompt_sha256: "0123abcd0123abcd", derived_from: null, usage: null, approved_by: "automatic", approved_at: "2026-10-17T12:00:00Z", requested_minutes: 60,
      approval: { by: "automatic", at: "2026-10-17T12:00:00Z", draft_version_id: 9, cost_eur: 1.52, evidence_audit: "ok" },
      evidence_audit: {
        status: "ok", verdict: "issues", report_only: true, note: "Report only: nothing was changed.",
        models: ["gpt-6.1-sol"], summary: "Checked acts 1-7 against the supplied facts.",
        counts: { severity: { high: 1, low: 1 }, kind: { wrong_detail: 1, distortion: 1 } },
        usage: { calls: 4, tokens_in: 520000, tokens_cached: 340000, tokens_reasoning: 20000, tokens_out: 24000, cost_eur: 0.91 },
        chunks: [],
        items: [
          { id: 1, severity: "high", kind: "wrong_detail", act: "act1", quote: "Andrew Borden was 71 years old.", claim: "Andrew was 71.", evidence_ids: ["F001"], evidence: "He was 70.", problem: "Age differs.", correction: "Say 70.", evidence_facts: [{ id: "F001", text: "Andrew Borden was 70 years old." }] },
          { id: 2, severity: "low", kind: "distortion", act: "act2", quote: "about a minute", claim: "A minute.", evidence_ids: [], evidence: "Not stated.", problem: "Rounded.", correction: "remove", evidence_facts: [] },
        ],
      },
      draft_checks: {
        numbers_and_names: { unsupported_figures: [{ figure: "1893", context: "in 1893 he left" }], names_not_in_evidence: ["Hosea Knowlton"], model_calls: 0 },
        coverage: { facts_in_pack: 156, told: 140, not_told: ["F150"], key_facts_not_told: ["F150"], model_calls: 0 },
      } },
    retired_method: [
      { id: 27, kind: "master_inspect", status: "retired", cost_eur: 3.83, round: 0, created_at: "2026-10-17T10:45:00Z", retired: { reason: "The fact-check loop was retired on 2026-10-17.", at: "2026-10-17T18:00:00Z", was: "stage_output" } },
      { id: 36, kind: "english_master", status: "retired", cost_eur: 11.6, round: null, created_at: "2026-10-17T11:30:00Z", retired: { reason: "The fact-check loop was retired on 2026-10-17.", at: "2026-10-17T18:00:00Z", was: "stopped" } },
    ],
    languages: [
      {
        language: "fa",
        direction: "rtl",
        stages: {
          rewrite: { id: 21, stage: "rewrite", language: "fa", status: "stage_output", model: "gpt-6.1-sol", reasoning_effort: "high", minutes: 66.0, words: 8910, cost_eur: 0.72, prompt: "language_pipeline/rewrite_writer", prompt_sha256: "abcdef0123456789", derived_from: { id: 9, kind: "draft", language: "en", prompt_sha256: "0123abcd" }, usage: null, approved_by: null, approved_at: null, requested_minutes: 60 },
          inspection: { id: 22, stage: "inspection", language: "fa", status: "stage_output", model: "gpt-6.1-sol", reasoning_effort: "high", minutes: 0.0, words: 0, cost_eur: 0.09, prompt: "language_pipeline/inspection_writer", prompt_sha256: "abcdef0123456789", derived_from: { id: 9, kind: "draft", language: "en", prompt_sha256: "0123abcd" }, usage: null, approved_by: null, approved_at: null, requested_minutes: 60 },
          final: { id: 31, stage: "final", language: "fa", status: "ready", model: "gpt-6.1-sol", reasoning_effort: "high", minutes: 62.4, words: 8424, cost_eur: 0.61, prompt: "language_pipeline/final_writer", prompt_sha256: "abcdef0123456789", derived_from: { id: 9, kind: "draft", language: "en", prompt_sha256: "0123abcd" }, usage: null, approved_by: "automatic", approved_at: "2026-10-17T12:30:00Z", requested_minutes: 60, length_corrected: true, acts_changed: ["act2"], corrects_version_id: 30 },
        },
        critique: {
          verdict: "revise",
          scores: { native_naturalness: 82, faithfulness_to_evidence: 95 },
          items: [{ id: 1, severity: "high", kind: "translationese", act: "act1", quote: "جمله نمونه", problem: "English word order", instruction: "Re-tell it" }],
          summary: "Strong voice; two calqued sentences.",
        },
        gates: { pass: true, failures: [], details: [], reports: { markers: { pass: true }, length: { pass: true }, originality: { pass: true } } },
        gate_history: [{ words: 8424, failures: [] }],
        blocking_gates: ["markers", "length"],
        automatic_approval: true,
        evidence_audit: { status: "ok", verdict: "issues", report_only: true, note: "Report only: nothing was changed.", models: ["gpt-6.1-sol"], summary: "Checked the final.", counts: { severity: { medium: 1 }, kind: { unsupported: 1 } }, usage: { calls: 4, tokens_in: 1, tokens_cached: 0, tokens_reasoning: 0, tokens_out: 1, cost_eur: 0.7 }, chunks: [], items: [{ id: 1, severity: "medium", kind: "unsupported", act: "act2", quote: "a quiet street", claim: "The street was quiet.", evidence_ids: [], evidence: "Not stated.", problem: "Added in the final.", correction: "remove", evidence_facts: [] }] },
        act_lengths: { act1: { target: 4200, words: 4150, ratio: 0.99, within_tolerance: true }, act2: { target: 4300, words: 4274, ratio: 0.99, within_tolerance: true } },
        length_correction: { status: "stored", corrects_version_id: 30, acts_changed: ["act2"], not_inspected: true,
          shortfalls: [{ act_id: "act1", current_words: 4150, target_words: 4200, shortfall_words: 50, master_words: 4100, qualifies: false }, { act_id: "act2", current_words: 3400, target_words: 4300, shortfall_words: 900, master_words: 5200, qualifies: true }],
          repetition: { act2: { trigram_before: 0.04, trigram_after: 0.06, likely_padded: true } } },
        fact_report: { added: { figures: [{ figure: "1894", context: "in 1894 the trial" }], names: [] }, missing: { figures: ["1893"], names: [], evidence_names: [] }, master_figures: 30, film_figures: 29, master_names: 0, film_names: 0, blocking: false, model_calls: 0, script_hygiene: { persian_letters: { count: 2, example: { line: 3, column: 5, context: "كتب ک" } }, tatweel: { count: 0, example: null }, blocking: false, model_calls: 0 }, names_compared: false, note: "Numbers only. Names are not compared (transliteration between scripts cannot be matched reliably)." },
        originality: { pass: true, worst: { longest_run: 4, ngram_share: 0.004 }, near_verbatim_count: 0, transcripts_compared: 12 },
        approved: true,
        final_version_id: 31,
      },
      { language: "en", direction: "ltr", stages: {}, critique: null, gates: null, gate_history: null, blocking_gates: ["markers", "length"], automatic_approval: false, fact_report: null, act_lengths: null, evidence_audit: null, length_correction: null, originality: null, approved: false, final_version_id: null },
      { language: "de", direction: "ltr", stages: {}, critique: null, gates: null, gate_history: null, originality: null, approved: false, final_version_id: null },
      { language: "ar", direction: "rtl", stages: {}, critique: null, gates: null, gate_history: null, originality: null, approved: false, final_version_id: null },
    ],
    ledger: [
      { stage: "language_draft", language: null, calls: 42, tokens_in: 8400000, tokens_cached: 7700000, tokens_reasoning: 0, tokens_out: 21000, eur: 1.52, cache_share: 0.917, cache_saving_eur: 13.1 },
      { stage: "language_final", language: "fa", calls: 1, tokens_in: 52000, tokens_cached: 30000, tokens_reasoning: 12000, tokens_out: 21000, eur: 0.61, cache_share: 0.577, cache_saving_eur: 0.05 },
    ],
    cost_total_eur: 2.13,
    calls_total: 43,
  },
  glossary: {
    languages: ["fa", "en", "de", "ar"],
    approved: { fa: false, en: true, de: false, ar: false },
    entities: [
      {
        entity: "Abby Borden",
        kind: "people",
        evidence_ids: ["F001", "F002"],
        languages: {
          fa: { spellings: ["ابی بوردن", "بوردن"], status: "proposed", origin: "agent" },
          en: { spellings: ["Abby Borden", "Borden"], status: "approved", origin: "evidence" },
          de: { spellings: ["Abby Borden"], status: "proposed", origin: "agent" },
          ar: { spellings: ["آبي بوردن"], status: "proposed", origin: "agent" },
        },
      },
    ],
  },
  languagePipelineEmpty: {
    draft: null,
    retired_method: [],
    languages: ["fa", "en", "de", "ar"].map((language) => ({ language, stages: {} })),
    ledger: [],
    cost_total_eur: 0,
  },
  languagePipelineEstimate: {
    note: "ESTIMATE from planning figures, not a price and not a spend. The actual cost is the ledger.",
    estimate: true, assumptions: "planning figures", large_calls: 13, calls: 60, lines: [],
    total_eur: 4.2, total_tokens_in: 602937, total_tokens_out: 350746,
  },
  researchLanguages: {
    production_languages: ["fa", "en", "de", "ar"],
    total_sources: 3,
    languages: {
      en: { searched: true, queries: ["keeper disappearance 1900"], sources: 2, full_text_sources: 1, evidence_items: 3, unique_evidence: 2 },
      de: { searched: true, queries: [], sources: 0, full_text_sources: 0, evidence_items: 0, unique_evidence: 0 },
      fa: { searched: true, queries: ["ناوگان فانوس"], sources: 1, full_text_sources: 0, evidence_items: 1, unique_evidence: 1 },
      ar: { searched: true, queries: [], sources: 0, full_text_sources: 0, evidence_items: 0, unique_evidence: 0 },
    },
  },
  researchJobs: [] as unknown[],
  agentRuns: [
    {
      id: 1,
      case_id: 1,
      agent_name: "Writer",
      status: "completed",
      started_at: "2024-06-02T09:00:00Z",
      completed_at: "2024-06-02T09:01:00Z",
      duration_ms: 60000,
      input_summary: "target=45min lang=en",
      output_summary: "words=2400",
      error: null,
      provider: "apimaster",
      model: "google/gemini-3.8-flash",
      role: "writer",
      temperature: 0.7,
      fallback_used: false,
      input_tokens: 10000,
      output_tokens: 6000,
      total_tokens: 16000,
      estimated_cost_usd: 0.3,
      generation_id: "gen-1",
      text_hash: "abc123",
    },
    {
      id: 2,
      case_id: 1,
      agent_name: "Engagement Critic",
      status: "failed",
      started_at: "2024-06-02T09:02:00Z",
      completed_at: "2024-06-02T09:02:10Z",
      duration_ms: 10000,
      input_summary: null,
      output_summary: null,
      error: "Rate limited",
      provider: "apimaster",
      model: "anthropic/claude-sonnet-5.5",
      role: "engagement_critic",
      temperature: null,
      fallback_used: true,
      input_tokens: null,
      output_tokens: null,
      total_tokens: null,
      estimated_cost_usd: null,
      generation_id: null,
      text_hash: null,
    },
  ],
  settings: {
    generation: {
      provider: "apimaster",
      configured: true,
      models: { cheap: "openai/gpt-5.6-luna", writer: "google/gemini-3.8-flash", premium: "anthropic/claude-sonnet-5.5" },
      routing: { writer: "writer", engagement_critic: "premium" },
      fallbacks: {},
    },
    search: { provider: "tavily", configured: true },
    youtube: { configured: false },
    research_provider: { provider: "truecrime", configured: true },
    multilingual: {
      research_languages: ["en", "de", "fa", "ar"],
      production_languages: ["fa", "en", "de", "ar"],
      research_only_languages: [],
      available_languages: { en: "English", de: "German", fa: "Persian", ar: "Arabic", ja: "Japanese" },
    },
    database: { connected: true, url: "sqlite" },
  },
  researchStatus: {
    provider: "truecrime",
    engine: "truecrime_search_engine",
    configured: true,
    reachable: true,
    search_backend: { backend: "brave", healthy: true },
    fetcher: { configured: true, rate_limited: true, cache_enabled: true },
    embedding: { model: "test-embed", configured: true },
    llm: { provider: "apimaster", configured: true },
  },
  dbOverview: {
    cases: { count: 1, items: [{ id: 1, title: CASE.title, status: "story_ready", created_at: CASE.created_at }] },
    sources: { count: 3, items: [] },
    facts: { count: 4, items: [] },
    contradictions: { count: 1, items: [] },
    stories: { count: 2, items: [] },
        research_jobs: { count: 0, items: [] },
    agent_runs: { count: 2, items: [] },
    discovery_history: { count: 0, items: [] },
  },
  shortFormSettings: {
    candidate_multiplier: 1.75,
    publish_target: 8,
    hard_max_seconds: 60,
    candidate_count: 14,
    distribution: {
      youtube_short: { enabled: true, count: 5, min_seconds: 20, target_seconds: 35, max_seconds: 60 },
      instagram_reel: { enabled: true, count: 6, min_seconds: 20, target_seconds: 30, max_seconds: 45 },
      facebook_reel: { enabled: true, count: 5, min_seconds: 25, target_seconds: 35, max_seconds: 60 },
      tiktok_video: { enabled: true, count: 8, min_seconds: 15, target_seconds: 28, max_seconds: 45 },
    },
  },
  shortFormWorkspace: {
    defaults: {
      min_count: 3, max_count: 5, target_count: 4,
      min_seconds: 20, max_seconds: 60, target_seconds: 35,
      width: 1080, height: 1920, captions_burned_in: true,
      default_visibility: "private", languages: ["en", "de"],
    },
    shorts_enabled: true,
    case: { id: 1, title: CASE.title, job_id: 42, film_status: "completed" },
    languages: ["en", "de"],
    candidates: [{
      id: 101, stable_key: "SFC_demo_01", concept_type: "DOCUMENT_MOMENT",
      source_beat_ids: ["B001"], source_chapter_ids: [], source_start_seconds: 12,
      source_end_seconds: 42, hook: "The log fixes the last known time.",
      cta: "Watch the full evidence-led story.", status: "selected",
      approval_state: "pending", rejection_reason: null,
      gate_results: { RevealFirewall: { passed: true }, EpistemicChecker: { passed: true }, RightsChecker: { passed: true } },
    }],
    renders: [{
      id: 201, candidate_id: 101, language: "en", status: "done", approval_state: "pending",
      caption_text: "The log fixes the last known time.", cta: "Watch the full evidence-led story.",
      video_url: "/api/short-form/files/201/video", cover_url: "/api/short-form/files/201/cover",
      captions_url: "/api/short-form/files/201/captions", gate_results: { RightsChecker: { passed: true } }, error: null,
    }, {
      id: 202, candidate_id: 101, language: "de", status: "done", approval_state: "pending",
      caption_text: "Das Protokoll legt die letzte bekannte Zeit fest.", cta: "Sieh die vollständige faktenbasierte Geschichte.",
      video_url: "/api/short-form/files/202/video", cover_url: "/api/short-form/files/202/cover",
      captions_url: "/api/short-form/files/202/captions", gate_results: { RightsChecker: { passed: true } }, error: null,
    }],
  },
  shortFormMetrics: {
    metric_rows: 15,
    comparison_basis: "concept_type",
    attribution_status: "unavailable",
    comparisons: [
      { concept_type: "verdict-countdown", total_views: 5210, platforms: ["instagram_reel", "tiktok_video", "youtube_short"], average_completion_rate: 0.62 },
      { concept_type: "dispatch-audio-question", total_views: 3260, platforms: ["instagram_reel", "tiktok_video", "youtube_short"], average_completion_rate: 0.51 },
    ],
  },
  discoveryHistory: [] as unknown[],
  discoveryCountries: [
    { code: "DE", name: "Germany", aliases: [] },
    { code: "FR", name: "France", aliases: [] },
    { code: "US", name: "United States", aliases: [] },
  ],
  discoveryCostEstimate: {
    count: 1, based_on_runs: 3, estimated_eur: 0.02, currency: "EUR",
    note: "Average of the last 3 completed discovery runs' real cost per requested case.",
  },
  discoverySearches: [] as unknown[],
  timeline: [
    {
      id: 21,
      case_id: 1,
      claim: "The keeper was last seen on December 15, 1900.",
      original_claim: null,
      original_language: "en",
      narrative_value: "turning_point",
      category: "timeline",
      confidence: 0.9,
      disputed: false,
      event_date: "1900-12-15",
      source_ids: [11],
    },
  ],
};


const namingCandidate = (id: number, language: string, title: string, rank: number, extra: Record<string, unknown> = {}) => ({
  id, case_id: 1, language, title, status: "candidate", rejection_reason: null, origin: "generated",
  collision_status: "clear", collision_with: null, near_collision_score: 41, semantic_collision_score: 0.31,
  memorability: 0.72, curiosity: 0.7, specificity: 0.81, brevity: 1, sensationalism_risk: 0.1,
  spoiler_risk: 0.1, epistemic_risk: 0.1, native_quality: 0.9, rank, recommended: rank === 1,
  angle: "lamp", reason: "ok", ...extra,
});
const namingLang = (language: string, titles: string[], ytitle: string | null) => ({
  candidates: titles.map((t, i) => namingCandidate(10 * (i + 1) + language.length, language, t, i + 1)),
  rejected: [],
  target: 7,
  short_by: Math.max(7 - titles.length, 0),
  identity: ytitle
    ? { id: 1, case_uid: "CASE_8f21c7", episode_sequence: 273, channel_id: "en", language,
        editorial_title: titles[0], resolution_status: "UNSOLVED", resolution_label: "Unsolved",
        youtube_title: ytitle, title_family_id: "TF_1", thumbnail_status_label: "Unsolved",
        published: false, published_title: null, title_version: 1 }
    : null,
});
export const namingFixture = {
  case_id: 1, case_uid: "CASE_8f21c7", title_family_id: "TF_1",
  editorial_concept: "a lamp that was left burning", externally_verified: false,
  provenance: { case_resolution_status: "UNSOLVED", public_status: "unsolved", resolution_confidence: 0.97, needs_review: false },
  corpus: { case_title: 3, source_title: 12 },
  languages: {
    en: namingLang("en", ["The Lantern Of Keeper Point", "Seven Doors Harbor"], "The Lantern Of Keeper Point (Unsolved) | ClueVera"),
    de: namingLang("de", ["Die Laterne von Keeper Point"], null),
    fa: namingLang("fa", ["فانوس سلله"], null),
    ar: namingLang("ar", ["فانوس سيله"], null),
  },
};

export const thumbnailFixture = {
  id: 1, case_id: 1, language: "en", version: 1, status: "draft",
  brief: { channel: "ClueVera", resolution_label: "Unsolved", thumbnail_text: "",
    host: { person: "Fereidoun", side: "left", size: "large", pose: "calm_right", outfit_id: "OUTFIT_TC_03" },
    case_visuals: [{ asset_id: 7, kind: "victim", role: "primary" }] },
  critic: { verdict: "needs_review", vision_checked: false, failures: [], checks: [],
    scorecard: { host_visibility: 0.9, case_visual_clarity: 0.8, brand_consistency: 0.8, cleanliness: 1,
      curiosity: null, authenticity: null, readability: 1, crowding: 0, spoiler_risk: 0,
      misleading_risk: 0, automation_feel: null } },
  outfit_id: "OUTFIT_TC_03", status_label: "Unsolved", image_url: "/api/thumbnails/1/image",
};
export const thumbnailOptionsFixture = {
  outfit_id: "OUTFIT_TC_03", host_error: null,
  outfits: [{ id: "OUTFIT_TC_03", label: "Studio Outfit", pose_count: 2 }, { id: "OUTFIT_TC_04", label: "Field Jacket", pose_count: 3 }],
  host_poses: [{ id: "HOST_TC_03_l", pose: "calm_left", facing: "left" }, { id: "HOST_TC_03_r", pose: "calm_right", facing: "right" }],
  usable: [{ asset_id: 7, code: "VIS_007", kind: "victim", title: "Portrait", rights: "public_domain", tier: 2, thumb: "/api/visuals/7/file?thumb=1" }],
  rejected: [{ asset_id: 8, code: "VIS_008", kind: "place", title: "Harbor", rights: "unknown", tier: 3, thumb: "/api/visuals/8/file?thumb=1", reason: "rights: unknown (needs editorial approval)" }],
};
const PIXEL = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

function json(route: import("@playwright/test").Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export interface MockOptions {
  dashboard?: unknown;
  dashboardStatus?: number;
  /** Per-request responses for /api/dashboard, in call order. */
  dashboardSequence?: { status: number; body: unknown }[];
  languagePipeline?: unknown;
  researchJob?: (call: number) => unknown;
}

/**
 * Mock every /api/** endpoint with fixture data. Pass overrides to
 * exercise failure/alternate states.
 */
export async function mockApi(page: Page, opts: MockOptions = {}) {
  const jobCalls = new Map<string, number>();
  let shortQueueCalls = 0;
  let shortManualStarted = false;
  let shortManualPolls = 0;
  const backup = {
    settings: { enabled: false, dir: "", every_hours: 24, start_time: "02:00", keep_last: 14 },
    runs: [] as Record<string, unknown>[],
  };
  let dashboardCalls = 0;
  let youtubeClips = false;

  const reviewQueueItems = [
    {
      group_id: 0, lead: "VIS_101", subject_type: "person", confidence: 0.55,
      asset_codes: ["VIS_101"], entity_key: "bryan_kohberger", entity_name: "Bryan Kohberger",
      beats: [{ beat_id: "B04", summary: "The suspect was arrested in Pennsylvania." },
        { beat_id: "B09", summary: "He appeared in court for the first time." }],
      beats_count: 2, verifier_reason: "A young man in a dark jacket, identity from the page caption only.",
      asset: { id: 101, asset_id: "VIS_101", title: "Suspect photo", caption: "Caption claims Bryan Kohberger",
        subject_type: "person", image_url: "/api/visuals/101/file", verification_confidence: 0.55 },
    },
    {
      group_id: 1, lead: "VIS_205", subject_type: "object", confidence: 0.5,
      asset_codes: ["VIS_205"], entity_key: "kabar_sheath", entity_name: "Ka-Bar sheath",
      beats: [{ beat_id: "B04", summary: "Police found no murder weapon, only a sheath." }],
      beats_count: 1, verifier_reason: "A leather knife sheath on an evidence table.",
      asset: { id: 205, asset_id: "VIS_205", title: "Evidence photo", caption: null,
        subject_type: "object", image_url: "/api/visuals/205/file", verification_confidence: 0.5 },
    },
  ];
  const reviewQueueState = {
    body: () => ({
      before: reviewQueueItems.length + 3, second_pass_pending: 3,
      items: reviewQueueItems.length, groups: reviewQueueItems.length, queue: reviewQueueItems,
    }),
    resolve: (assetId: number) => {
      const i = reviewQueueItems.findIndex((item) => item.asset.id === assetId);
      if (i >= 0) reviewQueueItems.splice(i, 1);
    },
  };

  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();

    if (path === "/api/backup" && method === "GET")
      return json(route, {
        settings: backup.settings,
        database: "sqlite",
        next_run_at: backup.settings.enabled ? "2026-10-12T00:00:00Z" : null,
        running: false,
        last_ok_at: (backup.runs.find((r) => r.status === "ok")?.started_at as string) ?? null,
        runs: backup.runs,
      });
    if (path === "/api/backup/settings" && method === "PUT") {
      backup.settings = route.request().postDataJSON();
      return json(route, backup.settings);
    }
    if (path === "/api/backup/run" && method === "POST") {
      const dir = route.request().postDataJSON().dir as string;
      backup.settings = { ...backup.settings, dir };
      const ok = !dir.includes("unplugged");
      const run = {
        id: backup.runs.length + 1,
        started_at: "2026-10-11T20:00:00Z",
        finished_at: "2026-10-11T20:00:02Z",
        trigger: "manual",
        status: ok ? "ok" : "failed",
        dest_dir: dir,
        file_path: ok ? `${dir}/truecrime-backup-20261011-220000.db` : null,
        size_bytes: ok ? 20557824 : null,
        error: ok ? null : `Folder not found: ${dir}. Is the drive connected?`,
      };
      backup.runs.unshift(run);
      return json(route, run);
    }
    if (path === "/api/ops/summary")
      return json(route, {
        window_hours: 24,
        total_calls: 42,
        total_errors: 3,
        total_cost_usd: 1.2345,
        providers: [
          { host: "api.llm.example", calls: 30, errors: 1, tokens_in: 1200, tokens_out: 800, cost_usd: 1.2, p50_ms: 900, p95_ms: 4000 },
          { host: "api.voice.example", calls: 12, errors: 2, tokens_in: 0, tokens_out: 0, cost_usd: 0.0345, p50_ms: 300, p95_ms: 700 },
        ],
        jobs: { running: 1, queued: 2, completed: 9 },
      });
    if (path === "/api/ops/provider-calls")
      return json(route, [
        { id: 7, at: "2026-10-11T09:00:00Z", host: "api.voice.example", method: "POST", path: "/v1/tts", status: 503, ok: false, duration_ms: 310, tokens_in: null, tokens_out: null, cost_usd: null, error: null, request_id: "abc123def456" },
      ]);
    if (path === "/api/documentary/visuals/youtube-clips") {
      if (method === "PUT") {
        youtubeClips = JSON.parse(route.request().postData() ?? "{}").enabled === true;
        return json(route, { enabled: youtubeClips });
      }
      return json(route, { enabled: youtubeClips, max_clip_seconds: 16, max_share: 0.08, max_source_seconds: 1800 });
    }
    if (path === "/api/auth/session") return json(route, { authenticated: true, auth_required: false });
    if (path === "/api/dashboard") {
      dashboardCalls += 1;
      const seq = opts.dashboardSequence;
      if (seq) {
        const step = seq[Math.min(dashboardCalls - 1, seq.length - 1)];
        return json(route, step.body, step.status);
      }
      if (opts.dashboardStatus && opts.dashboardStatus >= 400)
        return json(route, { detail: "Dashboard unavailable" }, opts.dashboardStatus);
      return json(route, opts.dashboard ?? fixtures.dashboard);
    }
    if (path === "/api/cases" && method === "GET") return json(route, fixtures.cases);
    if (path === "/api/cases" && method === "POST") return json(route, { id: 2, canonical_title: "New", slug: "new" });
    if (path === "/api/cases/1" && method === "GET") return json(route, fixtures.caseDetail);
    if (path === "/api/cases/1/documentary/jobs" && method === "GET") return json(route, []);
    if (path === "/api/cases/1" && method === "PATCH") return json(route, { id: 1, status: "archived" });
    if (path === "/api/cases/1/sources" && method === "GET") return json(route, fixtures.sources);
    if (path === "/api/cases/1/corpus-search" && method === "GET")
      return json(route, {
        query: url.searchParams.get("q") ?? "",
        language: url.searchParams.get("language"),
        case_id: 1,
        chunks_indexed: 12,
        dense_enabled: true,
        hits: [
          {
            chunk_id: 7,
            source_id: 11,
            source_title: "Lighthouse keepers logbook",
            source_url: "https://example.com/log",
            language: "en",
            text: "The keeper was last seen on December 15, 1900.",
            location: "p. 12",
            score: 0.81,
            bm25_score: 0.9,
            dense_score: 0.6,
          },
        ],
      });
    if (path === "/api/cases/1/thumbnails/options") return json(route, thumbnailOptionsFixture);
    if (path === "/api/cases/1/thumbnails/outfit" && method === "PATCH")
      return json(route, { case_id: 1, language: "en", outfit_id: route.request().postDataJSON().outfit_id });
    if (path === "/api/cases/1/thumbnails" && method === "GET") return json(route, [thumbnailFixture]);
    if (path === "/api/thumbnails/1/image") return route.fulfill({ status: 200, contentType: "image/png", body: PIXEL });
    if (path === "/api/cases/1/visuals/review-queue/ui" && method === "GET")
      return json(route, reviewQueueState.body());
    if (path === "/api/cases/1/visuals/review-queue/wrong-person" && method === "POST") {
      const body = route.request().postDataJSON() as { asset_id: number; entity_key: string };
      reviewQueueState.resolve(body.asset_id);
      return json(route, { asset_code: `VIS_${body.asset_id}`, entity_key: body.entity_key,
        not_this_note: "owner marked this a wrong person", dossier_updated: true });
    }
    if (path.match(/^\/api\/visuals\/\d+$/) && method === "PATCH") {
      const body = route.request().postDataJSON() as { verification?: string };
      const assetId = Number(path.split("/").pop());
      if (body.verification) reviewQueueState.resolve(assetId);
      return json(route, { id: assetId, verification: body.verification ?? "needs_review" });
    }
    if (path.startsWith("/api/visuals/")) return route.fulfill({ status: 200, contentType: "image/png", body: PIXEL });
    if (path === "/api/cases/1/naming" && method === "GET") return json(route, namingFixture);
    if (path === "/api/cases/1/facts") return json(route, fixtures.facts);
    if (path === "/api/cases/1/timeline") return json(route, fixtures.timeline);
    if (path === "/api/cases/1/contradictions") return json(route, fixtures.contradictions);
    if (path === "/api/cases/1/stories") return json(route, fixtures.stories);
    if (path === "/api/cases/1/stories/5") return json(route, fixtures.stories[0]);
    if (path === "/api/cases/1/research/languages") return json(route, fixtures.researchLanguages);
    if (path === "/api/cases/1/language-pipeline" && method === "GET")
      return json(route, opts.languagePipeline ?? fixtures.languagePipeline);
    if (path === "/api/cases/1/language-pipeline/glossary" && method === "GET") return json(route, fixtures.glossary);
    if (path === "/api/cases/1/language-pipeline/glossary/fa" && method === "PUT")
      return json(route, {
        ...fixtures.glossary,
        entities: [{ ...fixtures.glossary.entities[0], languages: {
          ...fixtures.glossary.entities[0].languages,
          fa: { spellings: route.request().postDataJSON().spellings, status: "proposed", origin: "owner" } } }],
      });
    if (path === "/api/cases/1/language-pipeline/glossary/fa/approve" && method === "POST")
      return json(route, { ...fixtures.glossary, approved: { ...fixtures.glossary.approved, fa: true } });
    if (path === "/api/cases/1/language-pipeline/estimate") return json(route, fixtures.languagePipelineEstimate);
    if (path === "/api/cases/1/agent-runs") return json(route, fixtures.agentRuns);
    if (path === "/api/cases/1/research" && method === "POST")
      return json(route, { job_id: 99, status: "queued" });
    if (/^\/api\/research-jobs\/\d+$/.test(path)) {
      const n = (jobCalls.get(path) ?? 0) + 1;
      jobCalls.set(path, n);
      const body = opts.researchJob
        ? opts.researchJob(n)
        : n < 2
          ? { id: 99, case_id: 1, provider: "truecrime", external_job_id: "x", job_type: "research", status: "running", result_summary: null, error: null, result: null, created_at: "2024-06-02T09:00:00Z", started_at: "2024-06-02T09:00:00Z", completed_at: null }
          : { id: 99, case_id: 1, provider: "truecrime", external_job_id: "x", job_type: "research", status: "completed", result_summary: null, error: null, result: { sources_added: 2 }, created_at: "2024-06-02T09:00:00Z", started_at: "2024-06-02T09:00:00Z", completed_at: "2024-06-02T09:05:00Z" };
      return json(route, body);
    }
    if (path === "/api/research-jobs") return json(route, fixtures.researchJobs);
    if (path === "/api/discovery/history") return json(route, fixtures.discoveryHistory);
    if (path === "/api/discovery/countries") return json(route, fixtures.discoveryCountries);
    if (path === "/api/discovery/cost-estimate") return json(route, fixtures.discoveryCostEstimate);
    if (path === "/api/discovery/searches") return json(route, fixtures.discoverySearches);
    if (/^\/api\/research-jobs\/\d+\/name$/.test(path) && method === "PATCH") {
      const body = route.request().postDataJSON();
      return json(route, { id: 41, display_name: body.display_name, name: body.display_name || "generated name" });
    }
    if (/^\/api\/discovery\/\d+\/(archive|delete|restore)$/.test(path) && method === "POST") {
      const state = path.endsWith("archive") ? "archived" : path.endsWith("delete") ? "deleted" : "suggested";
      return json(route, { id: Number(path.split("/")[3]), state });
    }
    if (path === "/api/topics/discover" && method === "POST")
      return json(route, { job_id: 77, status: "queued" });
    if (path === "/api/settings/status") return json(route, fixtures.settings);
    if (path === "/api/short-form/settings" && method === "GET")
      return json(route, fixtures.shortFormSettings);
    if (path === "/api/short-form/cases" && method === "GET")
      return json(route, [{ id: 1, title: "The Lighthouse Keeper Vanishing", produced_at: "2024-06-02T10:00:00Z", shorts: 2, published: 0, archived: false }]);
    if (path === "/api/short-form/workspace" && method === "GET")
      return json(route, fixtures.shortFormWorkspace);
    if (path === "/api/short-form/1/queue" && method === "GET") {
      shortQueueCalls += 1;
      if (!shortManualStarted) return json(route, []);
      shortManualPolls += 1;
      const status = shortManualPolls === 1 ? "queued" : shortManualPolls === 2 ? "running" : "done";
      return json(route, [{ id: 501, film_job_id: 42, language: "en", stage: "selection", status, attempts: 1, error: null, provider_cost_usd: 0.01, created_at: "2026-10-13T10:00:00Z", updated_at: "2026-10-13T10:00:01Z" }]);
    }
    if (path === "/api/short-form/1/create" && method === "POST") {
      shortManualStarted = true;
      return json(route, { queue_id: 501, film_job_id: 42, status: "queued" });
    }
    if (path === "/api/short-form/1/queue/501/retry" && method === "POST")
      return json(route, { id: 501, language: "en", status: "queued" });
    if ((path === "/api/short-form/files/201/video" || path === "/api/short-form/files/202/video") && method === "GET")
      return route.fulfill({ status: 200, contentType: "video/mp4", path: SHORT_101_VIDEO });
    if ((path === "/api/short-form/files/201/cover" || path === "/api/short-form/files/202/cover") && method === "GET")
      return route.fulfill({ status: 200, contentType: "image/png", path: SHORT_101_COVER });
    if (/^\/api\/short-form\/1\/candidates\/101\/preview$/.test(path) && method === "POST")
      return json(route, { preview_id: 901, action: "approve", target_id: 101, scope: [] });
    if (/^\/api\/short-form\/1\/renders\/201\/preview$/.test(path) && method === "POST")
      return json(route, { preview_id: 902, action: "approve", target_id: 201, scope: [] });
    if (/^\/api\/short-form\/1\/operations\/\d+\/apply$/.test(path) && method === "POST")
      return json(route, { status: "completed", preview_id: 901, changed_outside_scope: [] });
    if (path === "/api/short-form/1/settings" && method === "PATCH") {
      return json(route, { ...fixtures.shortFormWorkspace, shorts_enabled: true });
    }
    if (path === "/api/short-form/settings" && method === "PATCH") {
      const body = JSON.parse(route.request().postData() || "{}");
      return json(route, {
        ...fixtures.shortFormSettings,
        distribution: body.distribution,
        language_mode: body.language_mode,
        level: "episode_override",
      });
    }
    if (path === "/api/short-form/metrics" && method === "GET") return json(route, fixtures.shortFormMetrics);
    if (path === "/api/integrations/research/status") return json(route, fixtures.researchStatus);
    if (path === "/api/db/overview") return json(route, fixtures.dbOverview);
    if (path === "/api/overrides") return json(route, []);
    return json(route, { detail: `unmocked ${method} ${path}` }, 404);
  });
}
