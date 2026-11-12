import { test, expect } from "./test-base";
import { mockApi, PRODUCTION_LANGUAGES } from "./fixtures";

test.describe("Main Visual Planner picture review", () => {
  test("shows the first open picture with its requirement, five decisions and reasons", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await expect(page.getByTestId("review-queue-page")).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/1 \/ 2 · 2 left to review/);
    await expect(page.getByText("Bryan Kohberger").last()).toBeVisible();
    await expect(page.getByTestId("review-queue-list")).toContainText("Bryan Kohberger");
    await expect(page.getByTestId("review-queue-requirements")).toContainText("also offered for 1 more requirement");
    await expect(page.getByTestId("review-queue-decisions")).toContainText("Identity / context: needs_review");
    await expect(page.getByTestId("review-queue-reason")).toContainText("identity from the page caption only");
  });

  test("Approve records the decision, moves to the next picture and the position stays in the list", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: /^Approve \(A\)/ }).click();
    await expect(page.getByText(/Approved VIS_101 for 2 requirements/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/2 \/ 2 · 1 left to review.*1 decided/);
    await expect(page.getByText("Ka-Bar sheath").last()).toBeVisible();
  });

  test("Reject records the decision and a decided picture can be decided again", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: /^Reject \(R\)/ }).click();
    await expect(page.getByText(/Rejected VIS_101/)).toBeVisible();
    await page.getByRole("button", { name: "Previous", exact: false }).click();
    await expect(page.getByTestId("review-queue-verdict")).toContainText("Rejected");
    await page.getByRole("button", { name: /^Approve \(A\)/ }).click();
    await expect(page.getByText(/Approved VIS_101/)).toBeVisible();
  });

  test("keyboard shortcuts drive Approve and Reject until the queue is empty", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByTestId("review-queue-page").click();
    await page.keyboard.press("a");
    await expect(page.getByText(/Approved VIS_101/)).toBeVisible();
    await page.keyboard.press("r");
    await expect(page.getByText(/Rejected VIS_205/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/0 left to review.*2 decided/);
  });

  test("a picture can be checked as must-be-used and given a drawing priority, a clip only the checkbox", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByTestId("review-queue-must-use").click();
    await expect(page.getByTestId("review-queue-selection")).toContainText("Saved.");
    await expect(page.getByTestId("review-queue-must-use")).toBeChecked();
    await page.getByTestId("review-queue-draw-priority").selectOption("3");
    await expect(page.getByTestId("review-queue-draw-priority")).toHaveValue("3");
    await expect(page.getByRole("button", { name: "Trimmed & cut" })).toBeVisible();
    await page.getByRole("button", { name: /^Next \(N\)/ }).click();
    await page.getByRole("button", { name: "Previous", exact: false }).click();
    await expect(page.getByTestId("review-queue-draw-priority")).toHaveValue("3");
    await expect(page.getByTestId("review-queue-must-use")).toBeChecked();
    await expect(page.getByTestId("review-queue-chosen").first()).toContainText("3");
    await expect(page.getByTestId("review-queue-chosen-viewer")).toContainText("Drawing priority 3");
    await page.getByTestId("review-queue-draw-priority").selectOption("0");
    await expect(page.getByTestId("review-queue-chosen")).toHaveCount(0);
    await expect(page.getByTestId("review-queue-draw-priority")).toHaveValue("0");
  });

  test("opened from the portrait stop it explains what to do and shows the approved photos", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review?portraits=1");
    await expect(page.getByTestId("review-queue-portraits-note")).toContainText("Drawing priority 1");
    await expect(page.getByTestId("review-queue-shown").getByRole("button", { name: "Approved" })).toBeVisible();
  });

  test("the planner says how many portraits the film needs and the button makes them", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await expect(page.getByTestId("portraits-summary")).toContainText("The film needs 2 portraits");
    await expect(page.getByTestId("portrait-status-VIS_103")).toHaveText("reserve");
    await page.getByTestId("create-portraits").click();
    await expect(page.getByTestId("portrait-status-VIS_101")).toContainText("being drawn 20 %");
    await expect(page.getByTestId("create-portraits")).toBeDisabled();
    await expect(page.getByTestId("portraits-check")).toContainText("The planner ordered VIS_103 and checks again");
  });

  test("one button deletes the rejected pictures and one the rejected videos, each after a confirmation", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    const pictures = page.getByTestId("review-queue-delete-rejected-photo");
    const videos = page.getByTestId("review-queue-delete-rejected-video");
    await expect(pictures).toHaveText("Delete rejected pictures (3)");
    await expect(videos).toHaveText("Delete rejected videos (2)");
    page.once("dialog", (dialog) => dialog.dismiss());
    await pictures.click();
    await expect(pictures).toHaveText("Delete rejected pictures (3)");
    page.once("dialog", (dialog) => {
      expect(dialog.message()).toContain("Clear 3 rejected pictures? 7 rejected entries leave the list");
      void dialog.accept();
    });
    await pictures.click();
    await expect(page.getByText(/Cleared 7 rejected entries; deleted 3 rejected pictures \(5 MB freed\)/)).toBeVisible();
    await expect(pictures).toHaveText("Delete rejected pictures (0)");
    await expect(pictures).toBeDisabled();
    await expect(videos).toHaveText("Delete rejected videos (2)");
    page.once("dialog", (dialog) => {
      expect(dialog.message()).toContain("Clear 2 rejected videos? 4 rejected entries leave the list");
      void dialog.accept();
    });
    await videos.click();
    await expect(page.getByText(/Cleared 4 rejected entries; deleted 2 rejected videos \(40 MB freed\)/)).toBeVisible();
    await expect(videos).toBeDisabled();
  });

  test("Picture review is a tab of the documentary workspace, after Host and the Film cover", async ({ page }) => {
    await mockApi(page);
    const ok = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    await page.route("**/api/documentary/settings", (r) => r.fulfill(ok({
      languages: [...PRODUCTION_LANGUAGES], film_minutes: { min: 45, max: 120 }, pilot_seconds: 120, voices: {},
      channels: { en: { name: "ClueVera" } }, avatar_generation: { enabled: true },
      voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
      concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
      render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
      rights_profiles: { preview: [], publish: [] }, dynamic_eq: {},
    })));
    await page.route("**/api/cases/1/documentary", (r) => r.fulfill(ok({
      finals: [], master_version_id: null, film_minutes: { min: 45, max: 120 }, blueprint: null,
      audio_plan: null, languages: {}, visual_plan: null, jobs: [], visual_counts: {},
    })));
    await page.route("**/api/cases/1/costs", (r) => r.fulfill(ok({
      case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [],
    })));
    await page.goto("/documentary/1");
    await expect(page.getByRole("tab", { name: "Host" })).toBeVisible();
    const tabs = await page.getByRole("tab").allInnerTexts();
    expect(tabs.indexOf("Picture review")).toBe(tabs.indexOf("Film cover") + 1);
    expect(tabs.indexOf("Film cover")).toBe(tabs.indexOf("Host") + 1);
    await page.getByRole("tab", { name: "Picture review" }).click();
    await expect(page.getByRole("tab", { name: "Picture review", selected: true })).toBeVisible();
    await expect(page.getByTestId("review-queue-page")).toBeVisible();
    await expect(page.getByTestId("review-queue-delete-rejected-photo")).toBeVisible();
    await expect(page.locator("main")).toHaveCount(1);
  });
});
