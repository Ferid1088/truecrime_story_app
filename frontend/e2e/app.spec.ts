import { test, expect } from "./test-base";
import { mockApi, fixtures } from "./fixtures";

const jobBody = (
  status: string,
  result: unknown = null,
  error: string | null = null,
) => ({
  id: 77,
  case_id: null,
  provider: "truecrime",
  external_job_id: "x",
  job_type: "discovery",
  status,
  result_summary: null,
  error,
  result,
  created_at: "2024-06-02T09:00:00Z",
  started_at: "2024-06-02T09:00:00Z",
  completed_at: status === "completed" || status === "failed" ? "2024-06-02T09:05:00Z" : null,
});

const CANDIDATES = {
  candidates: [
    {
      candidate_id: 501,
      title: "The Vanishing of the Flannan Isles Keepers",
      rationale: "Three keepers vanished; the door was locked from inside.",
      narrative_potential: "high",
      languages_available: ["en", "de"],
      source_richness: "rich",
      angles: ["Storm theory", "Rogue wave", "Mutiny"],
      suggested_queries: [],
      already_covered: false,
      matched_existing_title: null,
      key_people: ["Thomas Marshall"],
      location: "Scotland",
      approximate_date: "1900-12",
      legal_risk: true,
      legal_risk_reasons: ["suspect not convicted"],
    },
  ],
  skipped_duplicates: 1,
};

test.describe("Dashboard", () => {
  test("loads with stats, recent cases, and agent activity — no skeleton remains", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByText("Total Cases")).toBeVisible();
    await expect(page.getByText("The Lighthouse Keeper Vanishing").first()).toBeVisible();
    await expect(page.getByText("Consistency Checker")).toBeVisible();
    await expect(page.locator(".animate-pulse")).toHaveCount(0);
  });

  test("shows error state with retry on API failure", async ({ page }) => {
    await mockApi(page, { dashboardStatus: 500 });
    await page.goto("/");
    await expect(page.getByText("Dashboard unavailable")).toBeVisible();
    await expect(page.getByRole("button", { name: /retry/i })).toBeVisible();
    await expect(page.locator(".animate-pulse")).toHaveCount(0);
  });

  test("error → retry recovers to success", async ({ page }) => {
    // StrictMode double-mounts, so the dashboard endpoint can be called
    // more than once — gate on a flag, not a call count. Route precedence
    // is last-registered-wins, so this must come after mockApi.
    await mockApi(page);
    let fail = true;
    await page.route("**/api/dashboard", (route) => {
      if (fail)
        return route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' });
      return route.fulfill({ contentType: "application/json", body: JSON.stringify(fixtures.dashboard) });
    });
    await page.goto("/");
    await expect(page.getByText("boom")).toBeVisible();
    fail = false;
    await page.getByRole("button", { name: /retry/i }).click();
    await expect(page.getByText("Total Cases")).toBeVisible();
  });
});

test.describe("Discover", () => {
  test("renders controls and empty state", async ({ page }) => {
    await mockApi(page);
    await page.goto("/discover");
    await expect(page.getByRole("button", { name: /discover new cases/i })).toBeVisible();
    await expect(page.getByText("No candidates yet")).toBeVisible();
  });

  test("job lifecycle: queued → running → candidates render", async ({ page }) => {
    await mockApi(page, {
      researchJob: (n) =>
        n < 2 ? jobBody("running") : jobBody("completed", CANDIDATES),
    });
    await page.goto("/discover");
    await page.getByRole("button", { name: /discover new cases/i }).click();
    await expect(page.getByTestId("discovery-job-status")).toBeVisible();
    await expect(page.getByText("The Vanishing of the Flannan Isles Keepers")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("1 duplicate rejected")).toBeVisible();
    await expect(page.getByText("Legal risk")).toBeVisible();
    await expect(page.getByText("suspect not convicted")).toBeVisible();
  });

  test("offers the historical pre-2000 discovery theme", async ({ page }) => {
    await mockApi(page);
    await page.goto("/discover");
    const theme = page.getByLabel("Theme");
    await expect(theme).toContainText("Historical cases (before 2000)");
    await theme.selectOption("historical cases (before 2000)");
    await expect(theme).toHaveValue("historical cases (before 2000)");
  });

  test("does not show completed when the returned job is missing from the API", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/topics/discover", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ job_id: 8, status: "queued" }),
      }),
    );
    await page.route("**/api/research-jobs/8", (route) =>
      route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Research job not found" }),
      }),
    );
    await page.goto("/discover");
    await page.getByRole("button", { name: /discover new cases/i }).click();
    await expect(page.getByText(/Discovery job #8 is not available from the API/)).toBeVisible();
    await expect(page.getByTestId("discovery-job-status")).toHaveCount(0);
  });

  test("failed job surfaces an error, no infinite spinner", async ({ page }) => {
    await mockApi(page, {
      researchJob: () => jobBody("failed", null, "Research provider unreachable"),
    });
    await page.goto("/discover");
    await page.getByRole("button", { name: /discover new cases/i }).click();
    await expect(page.getByText("Research provider unreachable", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".animate-pulse")).toHaveCount(0);
  });

  test("resumes a running discovery job after navigating away and back", async ({ page }) => {
    await mockApi(page, {
      researchJob: (n) =>
        n < 2 ? jobBody("running") : jobBody("completed", CANDIDATES),
    });
    await page.goto("/discover");
    await page.getByRole("button", { name: /discover new cases/i }).click();
    await expect(page.getByTestId("discovery-job-status")).toContainText("running");

    await page.goto("/cases");
    await page.goto("/discover");
    await expect(page.getByText("The Vanishing of the Flannan Isles Keepers")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("discovery-job-status")).toContainText("completed");
  });
});

test.describe("Short-Form settings", () => {
  test("opens production settings and saves the case Shorts switch", async ({ page }) => {
    await mockApi(page);
    await page.goto("/short-form");
    await expect(page.getByRole("heading", { name: "Short-Form Workspace" })).toBeVisible();
    await page.getByRole("tab", { name: "Settings", exact: true }).click();
    await expect(page.getByText("Production defaults")).toBeVisible();
    await page.getByRole("checkbox").click();
    await page.getByRole("button", { name: /save/i }).click();
    await expect(page.getByText("Saved case Shorts setting")).toBeVisible();
  });

  test("review page plays a persisted short and exposes editable approval controls", async ({ page }) => {
    await mockApi(page);
    await page.goto("/short-form/review");
    await expect(page.getByRole("heading", { name: /Review EN Short 101/ })).toBeVisible();
    await expect(page.locator("video")).toBeVisible();
    await expect(page.getByLabel("Caption")).toBeEnabled();
    await expect(page.getByRole("button", { name: /approve/i })).toBeEnabled();
  });
});

test.describe("Cases", () => {
  test("list renders and case opens", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases");
    await expect(page.getByText("The Lighthouse Keeper Vanishing")).toBeVisible();
    await page.getByText("The Lighthouse Keeper Vanishing").click();
    await expect(page).toHaveURL(/\/cases\/1/);
    await expect(page.getByRole("heading", { name: "The Lighthouse Keeper Vanishing" })).toBeVisible();
  });

  test("empty list shows empty state", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/cases?**", (route) =>
      route.fulfill({ contentType: "application/json", body: "[]" }),
    );
    await page.goto("/cases");
    await page.getByPlaceholder("Search cases…").fill("nonexistent");
    await expect(page.getByText("No cases match")).toBeVisible();
  });
});

test.describe("Case workspace", () => {
  test("all tabs render", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    for (const tab of [
      "Naming",
      "Thumbnail",
      "Pipeline",
      "Sources",
      "Corpus Search",
      "Facts",
      "Timeline",
      "Contradictions",
      "Story",
      "Research Languages",
      "Agent Runs",
      "Overview",
    ]) {
      await page.getByRole("button", { name: tab, exact: true }).click();
      await expect(page.locator(".animate-pulse")).toHaveCount(0, { timeout: 10_000 });
    }
  });

  test("naming tab shows candidates, collision status and the localized YouTube title", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Naming", exact: true }).click();
    await expect(page.getByText("The Lantern Of Keeper Point").first()).toBeVisible();
    await expect(page.getByTestId("youtube-title-en")).toHaveText(
      "The Lantern Of Keeper Point (Unsolved) | ClueVera",
    );
    await expect(page.getByText("not verified against the open internet")).toBeVisible();
    await expect(page.getByText("recommended").first()).toBeVisible();
    await expect(page.locator('[dir="rtl"]').first()).toBeVisible();
    // the internal sequence stays secondary, never in the public title
    await expect(page.getByTestId("youtube-title-en")).not.toContainText("273");
  });

  test("thumbnail tab shows the preview, badge, scorecard and why pictures were refused", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Thumbnail", exact: true }).click();
    await expect(page.getByTestId("thumbnail-preview")).toBeVisible();
    await page.getByLabel("Host outfit").selectOption("OUTFIT_TC_04");
    await expect(page.getByText("Outfit saved and audited")).toBeVisible();
    await expect(page.getByText("badge: Unsolved")).toBeVisible();
    await expect(page.getByText("Host outfit: OUTFIT_TC_04")).toBeVisible();
    await expect(page.getByTestId("scorecard").getByText("Automation feel (risk)")).toBeVisible();
    await page.getByText("1 pictures not usable").click();
    await expect(page.getByText("needs editorial approval")).toBeVisible();
    await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeEnabled();
  });

  test("corpus search returns hybrid hits with scores", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Corpus Search", exact: true }).click();
    await expect(page.getByText(/search the case corpus/i)).toBeVisible();
    await page.getByLabel("Corpus search query").fill("keeper disappearance");
    await page.getByRole("button", { name: /^search$/i }).click();
    await expect(page.getByText("Lighthouse keepers logbook")).toBeVisible();
    await expect(page.getByText(/bm25 0\.90/)).toBeVisible();
    await expect(page.getByText(/dense 0\.60/)).toBeVisible();
    await expect(page.getByText(/12 indexed chunks/)).toBeVisible();
  });

  test("facts tab renders claims and filters", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Facts", exact: true }).click();
    await expect(page.getByText("The keeper was last seen on December 15, 1900.")).toBeVisible();
    await expect(page.locator("td").filter({ hasText: "disputed" }).first()).toBeVisible();
  });

  test("research languages tab shows searched-but-empty state for de/ar", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Research Languages", exact: true }).click();
    await expect(page.getByText(/searched — no useful sources/i).first()).toBeVisible({ timeout: 10_000 });
  });

  test("Run Research polls the job and switches to Sources on completion", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: /run research/i }).click();
    await expect(page.getByText(/job #99|research running|queued/i)).toBeVisible();
    await expect(page.getByText("Keepers' log reprint")).toBeVisible({ timeout: 30_000 });
  });

  test("archive requires confirmation", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Archive", exact: true }).click();
    await expect(page.getByText("Archive case?")).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByText("Archive case?")).not.toBeVisible();
  });
});

test.describe("Language pipeline", () => {
  test("shows the shared draft once, a card per language, the critique, the gates and the cost table", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Story", exact: true }).click();
    await expect(page.getByText("Shared English draft (stage 1, written once)")).toBeVisible();
    await expect(page.getByText("claude-sonnet-5-5").first()).toBeVisible();
    await expect(page.getByText(/Re-told from the English draft v9/).first()).toBeVisible();
    await expect(page.getByTestId("draft-approval")).toContainText("approved automatically");
    await expect(page.getByTestId("retired-method")).toContainText("retired method");
    await expect(page.getByTestId("retired-method")).toContainText("2 rows of the fact-check loop");
    const checks = page.getByTestId("evidence-audit");
    await expect(checks.getByTestId("audit-model")).toContainText("model: gpt-6.1-sol");
    await expect(checks.getByTestId("audit-model")).toContainText("Report only, nothing was changed");
    await expect(checks.getByTestId("audit-counts")).toContainText("high 1 · medium 0 · low 1");
    await expect(checks.getByTestId("audit-item").first()).toContainText("Andrew Borden was 70 years old.");
    await expect(checks.getByTestId("audit-numbers")).toContainText("no model");
    await expect(checks.getByTestId("audit-numbers")).toContainText("Hosea Knowlton");
    await expect(checks.getByTestId("audit-coverage")).toContainText("140 of 156 facts told");
    await expect(page.getByRole("button", { name: /apply|correct/i })).toHaveCount(0);
    await expect(page.getByText("2 · Native rewrite").first()).toBeVisible();
    await expect(page.getByText("gpt-6.1-sol").first()).toBeVisible();
    await expect(page.getByText(/Inspector critique · 1 items/)).toBeVisible();
    await expect(page.getByText("all passed").first()).toBeVisible();
    await expect(page.getByText(/ESTIMATE, not a price and not a spend/)).toBeVisible();
    await expect(page.getByText("Cost per stage and language (actual ledger)")).toBeVisible();
    await expect(page.getByTestId("final-approval")).toContainText("approved automatically");
    await expect(page.getByRole("button", { name: "Approve final" })).toHaveCount(0);
    await expect(page.getByTestId("act-lengths")).toContainText("Words per act against the target");
    await page.getByTestId("act-lengths").locator("summary").click();
    await expect(page.getByTestId("act-lengths")).toContainText("4,274");
    await expect(page.getByTestId("final-audit")).toContainText("Evidence audit of this final text");
    await page.getByTestId("final-audit").locator("summary").click();
    await expect(page.getByTestId("final-audit")).toContainText("Added in the final.");
    await expect(page.getByTestId("length-correction")).toContainText("Bounded length correction");
    await expect(page.getByTestId("length-correction")).toContainText("stored");
    await expect(page.getByTestId("length-corrected-acts")).toContainText("length-corrected, not inspected");
    await expect(page.getByTestId("length-corrected-acts")).toContainText("act2");
    await expect(page.getByTestId("length-correction")).toContainText("likely padded");
    await expect(page.getByTestId("fact-report")).toContainText("1 added, 1 missing");
    await page.getByTestId("fact-report").locator("summary").click();
    await expect(page.getByTestId("fact-report")).toContainText("Names are not compared");
    await expect(page.getByTestId("script-hygiene")).toContainText("persian letters: 2 · first at line 3, column 5");
    await expect(page.getByText(/Blocking gates: markers, length/).first()).toBeVisible();
    await expect(page.getByRole("button", { name: /Start en/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Resume fa/ })).toBeVisible();
  });

  test("the owner reviews and approves the spellings of names and places per language", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Story", exact: true }).click();
    await expect(page.getByText("Names and places glossary")).toBeVisible();
    const fa = page.getByLabel("Abby Borden in fa");
    await expect(fa).toHaveValue("ابی بوردن | بوردن");
    await expect(page.getByLabel("Abby Borden in en")).toHaveValue("Abby Borden | Borden");
    await fa.fill("ابی بوردن | ابی بُردن");
    await fa.blur();
    await expect(page.getByLabel("Abby Borden in fa")).toHaveValue("ابی بوردن | ابی بُردن");
    const request = page.waitForRequest((r) => r.url().includes("/glossary/fa/approve") && r.method() === "POST");
    await page.getByRole("button", { name: /Approve Persian/i }).click();
    await request;
  });

  test("a case without a draft offers to run the pipeline", async ({ page }) => {
    await mockApi(page, { languagePipeline: fixtures.languagePipelineEmpty });
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Story", exact: true }).click();
    await expect(page.getByText(/Not written yet/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Run all languages" })).toBeEnabled();
  });
});

test.describe("Other pages", () => {
  for (const [path, text] of [
    ["/research", "Research"],
    ["/settings", "Settings"],
    ["/database", "Database"],
    ["/story-studio", "Story Studio"],
  ] as const) {
    test(`${path} renders without stuck skeleton`, async ({ page }) => {
      await mockApi(page);
      await page.goto(path);
      await expect(page.getByText(text, { exact: false }).first()).toBeVisible();
      await page.waitForTimeout(1500);
      await expect(page.locator(".animate-pulse")).toHaveCount(0);
    });
  }

  test("settings shows multilingual configuration", async ({ page }) => {
    await mockApi(page);
    await page.goto("/settings");
    await expect(page.getByText("Multilingual", { exact: true })).toBeVisible();
    await expect(page.getByText("Research languages")).toBeVisible();
  });
});

test.describe("RTL", () => {
  test("a right-to-left final text is shown right-to-left with its act markers left-to-right and intact", async ({ page }) => {
    const text = "[[ACT:act1]]\n\nنگهبان فانوس دریایی در نیمه شب ناپدید شد.\n\n[[ACT:act2]]\n\nهیچ‌کس ندید.";
    await mockApi(page);
    await page.route("**/api/cases/1/language-pipeline/fa/final", (route) =>
      route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ ...fixtures.languagePipeline.languages[0].stages.final, text }),
      }),
    );
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Story", exact: true }).click();
    await page.getByRole("button", { name: "Read final" }).click();
    const article = page.getByTestId("film-text");
    await expect(article).toHaveAttribute("dir", "rtl");
    await expect(article).toContainText("نگهبان فانوس دریایی");
    const markers = article.getByTestId("film-marker");
    await expect(markers).toHaveText(["[[ACT:act1]]", "[[ACT:act2]]"]);
    await expect(markers.first()).toHaveAttribute("dir", "ltr");
  });
});

test.describe("Hygiene", () => {
  test("no uncaught page errors on any main route", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await mockApi(page);
    for (const path of ["/", "/discover", "/cases", "/cases/1", "/research", "/settings", "/database", "/story-studio"]) {
      await page.goto(path);
      await page.waitForTimeout(1200);
    }
    expect(errors).toEqual([]);
  });
});
