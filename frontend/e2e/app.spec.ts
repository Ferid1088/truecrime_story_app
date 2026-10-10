import { test, expect } from "@playwright/test";
import { mockApi, fixtures } from "./fixtures";

const jobBody = (
  status: string,
  result: unknown = null,
  error: string | null = null,
) => ({
  id: 77,
  case_id: null,
  provider: "openrouter",
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
    await expect(page.getByText(/queued|running|searching the web/i)).toBeVisible();
    await expect(page.getByText("The Vanishing of the Flannan Isles Keepers")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText("1 duplicate rejected")).toBeVisible();
  });

  test("failed job surfaces an error, no infinite spinner", async ({ page }) => {
    await mockApi(page, {
      researchJob: () => jobBody("failed", null, "Research provider unreachable"),
    });
    await page.goto("/discover");
    await page.getByRole("button", { name: /discover new cases/i }).click();
    await expect(page.getByText("Research provider unreachable")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator(".animate-pulse")).toHaveCount(0);
  });
});

test.describe("Short-Form settings", () => {
  test("defaults, edit, invalid input, reset, persistence, RTL option", async ({ page }) => {
    await mockApi(page);
    await page.goto("/short-form");
    await expect(page.getByRole("heading", { name: "Short-Form" })).toBeVisible();
    const youtubeCount = page.getByRole("spinbutton", { name: "YouTube Short count" });
    await expect(youtubeCount).toHaveValue("5");
    await expect(page.getByText("14 candidates")).toBeVisible();

    await page.getByRole("button", { name: "Increase YouTube Short count" }).click();
    await expect(page.getByText("Unsaved changes")).toBeVisible();
    await expect(page.getByText(/capacity ~8/i)).toBeVisible();

    await youtubeCount.fill("21");
    await expect(page.getByText(/count must be 0–20/i)).toBeVisible();
    await expect(page.getByRole("button", { name: /save/i })).toBeDisabled();

    await page.getByRole("button", { name: "Reset YouTube Short" }).click();
    await expect(youtubeCount).toHaveValue("5");

    await page.getByLabel("Language").selectOption("all");
    await page.getByRole("button", { name: "Increase Instagram Reel count" }).click();
    await page.getByRole("button", { name: /save/i }).click();
    await expect(page.getByText("Saved")).toBeVisible();
    await expect(page.getByText("episode_override")).not.toBeVisible();
  });

  test("pilot review supports editing and approval", async ({ page }) => {
    await page.goto("/short-form/review");
    await expect(page.getByRole("heading", { name: "Review EN YouTube Short" })).toBeVisible();
    await page.getByLabel("Caption").fill("Updated pilot caption");
    await page.getByLabel("CTA").fill("Open the full story.");
    await page.getByRole("button", { name: /approve/i }).click();
    await expect(page.getByText("Approved")).toBeVisible();
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
      "Sources",
      "Corpus Search",
      "Facts",
      "Timeline",
      "Contradictions",
      "Story",
      "Research Languages",
      "Localizations",
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
    await expect(page.getByText("badge: Unsolved")).toBeVisible();
    await expect(page.getByText("host outfit: OUTFIT_TC_03")).toBeVisible();
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

test.describe("Master story & localization gating", () => {
  test("ready master shows quality info and enables localization Generate", async ({ page }) => {
    await mockApi(page);
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Localizations", exact: true }).click();
    await expect(page.getByText("English Master")).toBeVisible();
    await expect(page.getByText("ready", { exact: true }).first()).toBeVisible();
    await expect(page.getByText("Three Men and a Locked Door").first()).toBeVisible();
    const genButtons = page.getByRole("button", { name: "Generate", exact: true });
    await expect(genButtons.first()).toBeEnabled();
  });

  test("needs_revision master shows gate failures and blocks Generate", async ({ page }) => {
    await mockApi(page, { master: fixtures.needsRevisionMaster });
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Localizations", exact: true }).click();
    await expect(page.getByText("needs revision").first()).toBeVisible();
    await expect(page.getByText(/quality gate failure/i)).toBeVisible();
    await expect(page.getByText(/Act 3 engagement below threshold/)).toBeVisible();
    const genButtons = page.getByRole("button", { name: "Generate", exact: true });
    await expect(genButtons.first()).toBeDisabled();
  });

  test("missing master shows blocking empty state", async ({ page }) => {
    await mockApi(page, { masterStatus: 404 });
    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Localizations", exact: true }).click();
    await expect(page.getByText("No English Master yet")).toBeVisible();
    await expect(page.getByRole("button", { name: /open story studio/i })).toBeVisible();
  });
});

test.describe("Other pages", () => {
  for (const [path, text] of [
    ["/research", "Research"],
    ["/settings", "Settings"],
    ["/database", "Database"],
    ["/studio", "Story Studio"],
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
    await expect(page.getByText("Multilingual")).toBeVisible();
    await expect(page.getByText("Research languages")).toBeVisible();
  });
});

test.describe("RTL", () => {
  for (const [lang, text, label] of [
    ["fa", "نگهبان فانوس دریایی در نیمه شب ناپدید شد.", "Persian"],
    ["ar", "اختفى حارس منارة في منتصف الليل.", "Arabic"],
  ] as const) {
    test(`${label} story renders dir=rtl`, async ({ page }) => {
      const locMeta = {
        ...fixtures.masterStory.master,
        id: 91,
        version: 6,
        language: lang,
        kind: "localized",
        status: "ready",
        story_text: undefined,
      };
      delete (locMeta as Record<string, unknown>).story_text;
      await mockApi(page);
      await page.route("**/api/cases/1/localizations", (route) =>
        route.fulfill({ contentType: "application/json", body: JSON.stringify([locMeta]) }),
      );
      await page.route("**/api/localizations/91", (route) =>
        route.fulfill({
          contentType: "application/json",
          body: JSON.stringify({ ...locMeta, story_text: `${text}\n\n${text} — Lighthouse.` }),
        }),
      );
      await page.goto("/cases/1");
      await page.getByRole("button", { name: "Localizations", exact: true }).click();
      await page.getByRole("button", { name: /open v6/i }).click();
      const article = page.locator('article[dir="rtl"]');
      await expect(article).toBeVisible();
      await expect(article).toContainText(text);
    });
  }
});

test.describe("Hygiene", () => {
  test("no uncaught page errors on any main route", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await mockApi(page);
    for (const path of ["/", "/discover", "/cases", "/cases/1", "/research", "/settings", "/database", "/studio"]) {
      await page.goto(path);
      await page.waitForTimeout(1200);
    }
    expect(errors).toEqual([]);
  });
});
