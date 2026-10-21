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
  languages: ["en", "de", "fa", "ar"], cap_eur: 300, estimated_eur: 38.59,
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
  test("Start opens a dialog with all four languages and the printed estimate, then shows the run", async ({ page }) => {
    await mockApi(page, { researchJob: () => job });
    await page.route("**/api/autopilot/estimate**", (route) => route.fulfill(fulfil(ESTIMATE)));
    let started: unknown = null;
    await page.route("**/api/autopilot/runs", (route) => {
      started = route.request().postDataJSON();
      return route.fulfill(fulfil(RUN, 201));
    });
    await page.route("**/api/autopilot/runs/9", (route) => route.fulfill(fulfil(RUN)));
    await page.route("**/api/autopilot/runs/9/publish-requests", (route) => route.fulfill(fulfil(PUBLISH)));

    await page.goto("/discover");
    await page.getByRole("button", { name: /discover new cases/i }).click();
    await expect(page.getByText(CANDIDATE.title)).toBeVisible({ timeout: 30_000 });
    await page.getByTestId("start-autopilot").click();

    for (const lang of ["en", "de", "fa", "ar"])
      await expect(page.getByTestId(`start-language-${lang}`)).toBeChecked();
    await expect(page.getByTestId("start-language-en")).toBeDisabled();
    await expect(page.getByTestId("start-estimate")).toContainText("€38.59");
    await expect(page.getByTestId("start-estimate")).toContainText("€300.00");
    await page.getByTestId("start-confirm").click();

    await expect(page).toHaveURL(/\/autopilot\/9$/);
    expect(started).toEqual({ candidate_id: 501, languages: ["en", "de", "fa", "ar"] });
    await expect(page.getByTestId("run-status")).toHaveText("waiting owner");
    await expect(page.getByTestId("needs-you")).toContainText("voice refused");
    await expect(page.getByTestId("run-budget")).toContainText("€12.50 of the €300.00 cap");
    await expect(page.getByTestId("step-films")).toContainText("1 of 2 films finished");
    await expect(page.getByTestId("run-language-fa")).toContainText("چراغ خاموش شد");
    await expect(page.getByTestId("publish-queue")).toContainText("Nothing is sent");
    await page.getByRole("button", { name: "Request" }).click();
    await expect(page.getByRole("dialog")).toContainText('"sent": false');
  });

  test("a refused start shows the reason and stays on the page", async ({ page }) => {
    await mockApi(page, { researchJob: () => job });
    await page.route("**/api/autopilot/estimate**", (route) => route.fulfill(fulfil(ESTIMATE)));
    await page.route("**/api/autopilot/runs", (route) =>
      route.fulfill(fulfil({ detail: "A run for this case is already going." }, 409)));
    await page.goto("/discover");
    await page.getByRole("button", { name: /discover new cases/i }).click();
    await page.getByTestId("start-autopilot").click();
    await page.getByTestId("start-confirm").click();
    await expect(page.getByRole("dialog").getByRole("alert")).toContainText("already going");
    await expect(page).toHaveURL(/\/discover$/);
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
});
