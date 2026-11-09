import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const CANDIDATE = {
  candidate_id: 501,
  title: "The Vanishing of the Flannan Isles Keepers",
  rationale: "Three keepers vanished.",
  narrative_potential: "high",
  languages_available: ["en", "de"],
  source_richness: "rich",
  angles: [],
  suggested_queries: [],
  already_covered: false,
  matched_existing_title: null,
  key_people: [],
  location: "Scotland",
  approximate_date: "1900-12",
  legal_risk: false,
  legal_risk_reasons: [],
};

const job = {
  id: 77, case_id: null, provider: "truecrime", external_job_id: "x", job_type: "discovery",
  status: "completed", result_summary: null, error: null, result: { candidates: [CANDIDATE], skipped_duplicates: 0 },
  created_at: "2024-06-02T09:00:00Z", started_at: "2024-06-02T09:00:00Z", completed_at: "2024-06-02T09:05:00Z",
};

const ESTIMATE = {
  languages: ["en", "de", "fa", "ar"], cap_eur: 300, estimated_eur: 38.59, visual_research_eur: 6.4,
  range_eur: { low: 26.83, high: 47.23 }, pricing_last_verified: "2026-10-01", price_warnings: [],
};

const RUN = {
  id: 9, status: "waiting_owner", stage: "films", error: null,
  created_at: "2026-10-21T10:00:00Z", updated_at: "2026-10-21T10:05:00Z", completed_at: null,
  case: { id: 4, title: CANDIDATE.title }, estimate: ESTIMATE,
  budget: { cap_eur: 300, spent_eur: 12.5 },
  steps: [
    { name: "case", status: "done", detail: "case opened from the suggestion", at: null },
    { name: "research", status: "done", detail: null, at: null },
    { name: "texts", status: "done", detail: null, at: null },
    { name: "films", status: "running", detail: "1 of 2 films finished", at: null },
    { name: "titles", status: "pending", detail: null, at: null },
    { name: "shorts", status: "pending", detail: null, at: null },
    { name: "publish", status: "pending", detail: null, at: null },
  ],
  needs_you: [
    { key: "k", kind: "film_stopped", message: "The de film (job 3) is failed: voice refused", language: "de", blocking: true },
  ],
  languages: {
    en: { film_job: { id: 2, status: "completed", progress: 1, stage: null, error: null }, titles: [{ id: 1, title: "The Lamp Went Dark", selected: false }] },
    fa: { film_job: null, titles: [{ id: 2, title: "چراغ خاموش شد", selected: false }] },
  },
  publish_requests: 1,
};

const PUBLISH = {
  sent: 0,
  requests: [{
    id: 1, language: "en", platform: "youtube", kind: "film", short_id: null, status: "dry_run",
    scheduled_for: "2026-10-22T15:00:00Z", request: { title: "The Lamp Went Dark", sent: false },
  }],
};

const fulfil = (body: unknown, status = 200) => ({
  status, contentType: "application/json", body: JSON.stringify(body),
});

test.describe("Autopilot", () => {
  test("the case page saves languages and outfits, then the Autopilot button starts exactly those", async ({ page }) => {
    await mockApi(page);
    const outfit = (language: string) => ({
      language, selected: "existing:1",
      options: [{ key: "existing:1", name: "navy Suit", ref: `${language}/navySuit`, source: "case", status: "selected", selected: true, photos: [] }],
    });
    const production = (saved: boolean, languages: string[], run: unknown = null) => ({
      case_id: 1, name: { approved: true, title: CANDIDATE.title },
      allowed_languages: ["en", "de", "fa", "ar", "es", "it", "fr", "pt", "tr"], required_language: "en",
      languages, saved, outfits: ["en", "de", "fa", "ar", "es", "it", "fr", "pt", "tr"].map(outfit),
      estimate: saved ? ESTIMATE : null, blockers: saved ? [] : ["Save the languages to produce."], run,
    });
    let state = production(false, ["en", "de", "fa", "ar", "es", "it", "fr", "pt", "tr"]);
    let savedBody: { languages: string[] } | null = null;
    await page.route("**/api/cases/1/production", (route) => {
      if (route.request().method() === "PUT") {
        savedBody = route.request().postDataJSON();
        state = production(true, savedBody!.languages);
      }
      return route.fulfill(fulfil(state));
    });
    await page.route("**/api/cases/1/production/autopilot", (route) => {
      state = production(true, state.languages, { ...RUN, case: { id: 1, title: CANDIDATE.title } });
      return route.fulfill(fulfil(RUN, 201));
    });

    await page.goto("/cases/1");
    const panel = page.getByTestId("production-panel");
    await expect(panel).toBeVisible({ timeout: 30_000 });
    for (const lang of ["en", "de", "fa", "ar", "es", "it", "fr", "pt", "tr"]) {
      await expect(page.getByTestId(`production-language-${lang}`)).toBeChecked();      // all ticked by default
      await expect(page.getByTestId(`production-outfit-${lang}`)).toBeVisible();        // and each one has its outfit choice
    }
    await expect(page.getByTestId("production-language-en")).toBeDisabled();
    await expect(page.getByTestId("production-autopilot")).toBeDisabled();      // nothing saved yet
    for (const lang of ["ar", "it", "fr", "pt", "es"]) await page.getByTestId(`production-language-${lang}`).uncheck();   // skipped
    await expect(page.getByTestId("production-outfit-ar")).toHaveCount(0);
    await expect(page.getByTestId("production-outfit-tr")).toBeVisible();
    await page.getByTestId("production-save").click();
    await expect(page.getByTestId("production-estimate")).toContainText("€38.59");
    expect(savedBody).toMatchObject({ languages: ["en", "de", "fa", "tr"] });
    await expect(page.getByTestId("production-autopilot")).toBeEnabled();
    await page.getByTestId("production-autopilot").click();
    await expect(page.getByTestId("production-run-status")).toHaveText("waiting owner");
    await expect(page.getByTestId("production-needs")).toContainText("voice refused");
    await expect(page.getByTestId("production-film-fa")).toContainText("not started");
    await expect(page.getByTestId("production-language-de")).toBeDisabled();    // fixed while the run goes
  });

  test("the Autopilot button stays locked and says why until the English name is approved", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/cases/1/production", (route) => route.fulfill(fulfil({
      case_id: 1, name: { approved: false, title: CANDIDATE.title },
      allowed_languages: ["en", "de"], required_language: "en", languages: ["en", "de"], saved: true,
      outfits: [], estimate: null, blockers: ["Approve the case's English name first (Naming tab): nothing is produced before the case is confirmed."],
      run: null,
    })));
    await page.goto("/cases/1");
    await expect(page.getByTestId("production-name-status")).toHaveText("Name not approved");
    await expect(page.getByTestId("production-name-hint")).toContainText("Naming tab");
    await expect(page.getByTestId("production-autopilot")).toBeDisabled();
  });

  test("a suggestion is opened as a case, not started: the production choices are made on the case page", async ({ page }) => {
    await mockApi(page, { researchJob: () => job });
    await page.route("**/api/discovery/501/investigate", (route) => route.fulfill(fulfil({ id: 1, canonical_title: "x", slug: "x" })));
    await page.goto("/discover");
    await page.getByRole("button", { name: /discover new cases/i }).click();
    await expect(page.getByText(CANDIDATE.title)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("start-autopilot")).toHaveCount(0);
  });

  test("the run page shows every chosen language, the new ones too, and Persian's own review as a need", async ({ page }) => {
    await mockApi(page);
    const film = (status: string) => ({ id: 1, status, progress: 0, stage: null, error: null });
    const run = {
      ...RUN, status: "waiting_owner", needs_you: [
        { key: "films:fa:film_review", kind: "film_review", language: "fa", blocking: true,
          message: "Review and approve the fa film (its pictures and its render): the other languages go on without waiting for it." },
      ],
      languages: Object.fromEntries(["en", "fa", "es", "it", "fr", "pt", "tr"].map((l) =>
        [l, { film_job: film(l === "en" ? "completed" : "running"), titles: [] }])),
    };
    await page.route("**/api/autopilot/runs/9", (route) => route.fulfill(fulfil(run)));
    await page.route("**/api/autopilot/runs/9/publish-requests", (route) => route.fulfill(fulfil(PUBLISH)));
    await page.goto("/autopilot/9");
    for (const [lang, label] of [["es", "Spanish"], ["it", "Italian"], ["fr", "French"], ["pt", "Portuguese"], ["tr", "Turkish"]])
      await expect(page.getByTestId(`run-language-${lang}`)).toContainText(label);
    await expect(page.getByTestId("needs-you")).toContainText("the other languages go on without waiting");
  });

  test("Settings shows a not-connected placeholder per language and platform", async ({ page }) => {
    await mockApi(page);
    const channels = ["en", "de", "fa", "ar"].flatMap((language) =>
      ["youtube_short", "instagram_reel", "facebook_reel", "tiktok_video"].map((platform) => ({
        language, platform, handle: "", connected: false,
      })));
    await page.route("**/api/channel-settings", (route) => route.fulfill(fulfil({ channels })));
    let saved: unknown = null;
    await page.route("**/api/channel-settings/de/tiktok_video", (route) => {
      saved = route.request().postDataJSON();
      return route.fulfill(fulfil({ language: "de", platform: "tiktok_video", handle: "@wahr", connected: false }));
    });
    await page.goto("/settings");
    const card = page.getByTestId("channel-settings");
    await expect(card.getByText("Not connected")).toHaveCount(16);
    await card.getByLabel("Handle for German tiktok video").fill("@wahr");
    await card.getByRole("button", { name: "Save" }).nth(7).click();
    await expect(card.getByRole("status")).toContainText("Saved");
    expect(saved).toEqual({ handle: "@wahr" });
  });

  test("Home shows a light per phase with the reason, and Retry gives a red phase a fresh start", async ({ page }) => {
    await mockApi(page);
    const lights = (texts: "red" | "grey") => [
      { phase: "research", light: "green", reason: null, attempts: 1, can_retry: false },
      { phase: "texts", light: texts, reason: texts === "red" ? "The German text was not approved." : null, attempts: texts === "red" ? 3 : 0, can_retry: texts === "red" },
      { phase: "films", light: "grey", reason: null, attempts: 0, can_retry: false },
    ];
    let current = { ...RUN, status: "waiting_owner", health: lights("red") };
    let retried: string | null = null;
    await page.route("**/api/autopilot/runs", (route) => route.fulfill(fulfil({ runs: [current] })));
    await page.route("**/api/autopilot/runs/9/phases/*/retry", (route) => {
      retried = route.request().url().split("/phases/")[1].split("/")[0];
      current = { ...current, status: "running", health: lights("grey") };
      return route.fulfill(fulfil(current));
    });
    await page.goto("/");
    const texts = page.getByTestId("autopilot-phase-9-texts");
    await expect(texts).toContainText("The German text was not approved.", { timeout: 30_000 });
    await expect(texts.locator("[data-light]")).toHaveAttribute("data-light", "red");
    await expect(page.getByTestId("autopilot-phase-9-research").locator("[data-light]")).toHaveAttribute("data-light", "green");
    await expect(page.getByTestId("autopilot-phase-9-research").getByRole("button", { name: "Retry" })).toHaveCount(0);
    await texts.getByRole("button", { name: "Retry" }).click();
    await expect(texts.locator("[data-light]")).toHaveAttribute("data-light", "grey");
    expect(retried).toBe("texts");
    await expect(texts.getByRole("button", { name: "Retry" })).toHaveCount(0);
  });
});
