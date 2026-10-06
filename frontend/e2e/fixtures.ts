import type { Page } from "@playwright/test";

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
      research_provider: "openrouter",
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
      research_provider: "openrouter",
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
      kind: "master",
      master_version_id: null,
      derived_from_master_version: null,
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
      generation_provider: "openrouter",
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
  masterStory: {
    master: {
      id: 5,
      case_id: 1,
      version: 2,
      narrative_angle: JSON.stringify({
        title: "Three Men and a Locked Door",
        central_question: "What happened inside the lighthouse that night?",
      }),
      language: "en",
      kind: "master",
      master_version_id: null,
      derived_from_master_version: null,
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
      generation_provider: "openrouter",
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
    localizations: [],
  },
  needsRevisionMaster: {
    master: {
      id: 6,
      case_id: 1,
      version: 3,
      narrative_angle: null,
      language: "en",
      kind: "master",
      master_version_id: null,
      derived_from_master_version: null,
      native_quality_score: null,
      semantic_consistency_score: null,
      factual_consistency_score: null,
      engagement_score: 41,
      similarity_score: 0.2,
      similarity_status: "ok",
      status: "needs_revision",
      is_best: false,
      word_count: 1800,
      dimensions: {},
      problems: ["Act 3 engagement below threshold (42 < 55)", "2 unsupported claims"],
      rewrite_instructions: ["Strengthen the Act 3 reveal"],
      generation_provider: "openrouter",
      generation_model: "google/gemini-3.8-flash",
      text_hash: "def456",
      narrative_structure: null,
      created_at: "2024-06-03T09:00:00Z",
      story_text: "Draft text.",
    },
    localizations: [],
  },
  localizations: [],
  researchLanguages: {
    canonical_language: "en",
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
      provider: "openrouter",
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
      provider: "openrouter",
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
      provider: "openrouter",
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
      canonical_language: "en",
      localization_languages: ["de", "fa", "ar"],
    },
    database: { connected: true, url: "sqlite" },
  },
  researchStatus: {
    provider: "truecrime",
    engine: "truecrime_search_engine",
    configured: true,
    reachable: true,
    search_backend: { backend: "searxng", url: "http://localhost:8085", healthy: true },
    fetcher: { configured: true, rate_limited: true, cache_enabled: true },
    embedding: { model: "test-embed", configured: true },
    llm: { provider: "apimaster", configured: true },
  },
  // Historical provider — retired from active research, status kept for
  // display only.
  openrouterStatus: {
    provider: "openrouter",
    configured: false,
    reachable: false,
    active: false,
  },
  dbOverview: {
    cases: { count: 1, items: [{ id: 1, title: CASE.title, status: "story_ready", created_at: CASE.created_at }] },
    sources: { count: 3, items: [] },
    facts: { count: 4, items: [] },
    contradictions: { count: 1, items: [] },
    stories: { count: 2, items: [] },
    localizations: { count: 0, items: [] },
    research_jobs: { count: 0, items: [] },
    agent_runs: { count: 2, items: [] },
    discovery_history: { count: 0, items: [] },
  },
  discoveryHistory: [] as unknown[],
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

function json(route: import("@playwright/test").Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export interface MockOptions {
  dashboard?: unknown;
  dashboardStatus?: number;
  /** Per-request responses for /api/dashboard, in call order. */
  dashboardSequence?: { status: number; body: unknown }[];
  master?: unknown;
  masterStatus?: number;
  researchJob?: (call: number) => unknown;
}

/**
 * Mock every /api/** endpoint with fixture data. Pass overrides to
 * exercise failure/alternate states.
 */
export async function mockApi(page: Page, opts: MockOptions = {}) {
  const jobCalls = new Map<string, number>();
  let dashboardCalls = 0;

  await page.route("**/api/**", (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();

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
    if (path === "/api/cases/1/facts") return json(route, fixtures.facts);
    if (path === "/api/cases/1/timeline") return json(route, fixtures.timeline);
    if (path === "/api/cases/1/contradictions") return json(route, fixtures.contradictions);
    if (path === "/api/cases/1/stories") return json(route, fixtures.stories);
    if (path === "/api/cases/1/stories/5") return json(route, fixtures.stories[0]);
    if (path === "/api/cases/1/research/languages") return json(route, fixtures.researchLanguages);
    if (path === "/api/cases/1/master-story" && method === "GET") {
      if (opts.masterStatus && opts.masterStatus >= 400)
        return json(route, { detail: "No master story found" }, opts.masterStatus);
      return json(route, opts.master ?? fixtures.masterStory);
    }
    if (path === "/api/cases/1/localizations" && method === "GET")
      return json(route, fixtures.localizations);
    if (path === "/api/cases/1/agent-runs") return json(route, fixtures.agentRuns);
    if (path === "/api/cases/1/research" && method === "POST")
      return json(route, { job_id: 99, status: "queued" });
    if (/^\/api\/research-jobs\/\d+$/.test(path)) {
      const n = (jobCalls.get(path) ?? 0) + 1;
      jobCalls.set(path, n);
      const body = opts.researchJob
        ? opts.researchJob(n)
        : n < 2
          ? { id: 99, case_id: 1, provider: "openrouter", external_job_id: "x", job_type: "research", status: "running", result_summary: null, error: null, result: null, created_at: "2024-06-02T09:00:00Z", started_at: "2024-06-02T09:00:00Z", completed_at: null }
          : { id: 99, case_id: 1, provider: "openrouter", external_job_id: "x", job_type: "research", status: "completed", result_summary: null, error: null, result: { sources_added: 2 }, created_at: "2024-06-02T09:00:00Z", started_at: "2024-06-02T09:00:00Z", completed_at: "2024-06-02T09:05:00Z" };
      return json(route, body);
    }
    if (path === "/api/research-jobs") return json(route, fixtures.researchJobs);
    if (path === "/api/discovery/history") return json(route, fixtures.discoveryHistory);
    if (path === "/api/topics/discover" && method === "POST")
      return json(route, { job_id: 77, status: "queued" });
    if (path === "/api/settings/status") return json(route, fixtures.settings);
    if (path === "/api/integrations/research/status") return json(route, fixtures.researchStatus);
    if (path === "/api/integrations/openrouter/status") return json(route, fixtures.openrouterStatus);
    if (path === "/api/db/overview") return json(route, fixtures.dbOverview);
    return json(route, { detail: `unmocked ${method} ${path}` }, 404);
  });
}
