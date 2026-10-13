import { expect, test } from "./test-base";
import { fixtures } from "./fixtures";

const stages = [
  { name: "research", status: "done", detail: { summary: "Research saved" } },
  { name: "master_story", status: "done", detail: { summary: "Master story saved" } },
  { name: "spoken:de", status: "done", detail: { summary: "German spoken text" } },
  { name: "voice:de", status: "pending", detail: null },
];

const state = (stage: string, pending = false, automatic = false) => ({
  case_id: 1,
  job_id: 12,
  stage,
  status: stages.find((item) => item.name === stage)?.status ?? "pending",
  available_actions: pending ? ["edit", "run_automatically"] : ["continue", "redo_with_prompt", "edit", "run_automatically"],
  blocked_actions: {
    continue: pending ? "pending user edit or lock" : null,
    redo_with_prompt: pending ? "pending user edit or lock" : null,
    edit: null,
    run_automatically: null,
  },
  pending_user_edit: pending,
  automatic,
  artifacts: stage === "spoken:de"
    ? [{ id: 201, type: "spoken_version", language: "de", status: "ready" }]
    : [{ id: 101, type: stage === "master_story" ? "master_story" : "audio_blocks", language: null, status: "ready" }],
});

async function mockStageControlApi(page: import("@playwright/test").Page, staleApply = false) {
  let previewCount = 0;
  let resultStatus = "queued";
  const automaticStages = new Set<string>();
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    const method = route.request().method();
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/session") return json({ authenticated: true, auth_required: false });
    if (path === "/api/cases/1" && method === "GET") return json(fixtures.caseDetail);
    if (path === "/api/documentary/settings") return json({
      languages: ["en", "de", "fa", "ar"], film_minutes: { min: 45, max: 120 }, pilot_seconds: 60,
      voices: {}, pronunciation_check: { enabled: false, languages: [], max_rounds: 1 },
      voice_performance: { enabled: false, level_tags: {}, level_styles: {} },
      concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
      styles: {}, render: { profile: "preview", width: 1280, height: 720, fps: 25, crf: 23, preset: "fast", burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 6, show_credits: true },
      rights_profiles: {}, dynamic_eq: {},
    });
    if (path === "/api/cases/1/documentary") return json({
      masters: [{ id: 41, version: 1, language: "en", status: "approved", words: 900, kind: "master" }],
      master_version_id: 41, film_minutes: { min: 45, max: 120 }, blueprint: null, audio_plan: null,
      languages: { en: null, de: null, fa: null, ar: null }, visual_plan: null,
      jobs: [{ id: 12, case_id: 1, master_version_id: 41, mode: "full", languages: ["en", "de", "fa", "ar"],
        pilot_seconds: null, render_profile: "preview", refresh_visuals: false, from_zero: false,
        target_minutes: null, batch_id: null, production_type: "new", follow_up_id: null,
        status: "completed", stage: null, progress: 1, stages, result: {}, error: null,
        created_at: "2026-10-13T08:00:00Z", updated_at: "2026-10-13T08:01:00Z", completed_at: "2026-10-13T08:01:00Z" }],
      visual_counts: {},
    });
    if (path === "/api/cases/1/documentary/jobs") return json([{
      id: 12, case_id: 1, master_version_id: 41, mode: "full", languages: ["en", "de", "fa", "ar"],
      pilot_seconds: null, render_profile: "preview", refresh_visuals: false, from_zero: false,
      target_minutes: null, batch_id: null, production_type: "new", follow_up_id: null,
      status: "completed", stage: null, progress: 1, stages, result: {}, error: null,
      created_at: "2026-10-13T08:00:00Z", updated_at: "2026-10-13T08:01:00Z", completed_at: "2026-10-13T08:01:00Z",
    }]);
    if (path === "/api/cases/1/documentary/chapters") return json({
      id: 7, version: 1, status: "approved", blueprint_id: 3, languages: ["en", "de", "fa", "ar"],
      plan: { languages: ["en", "de", "fa", "ar"], cold_open: true, film_title: {},
        chapters: [{ act_id: "act-1", number: 1, first_beat: "b1", last_beat: "b2", title: { en: "Arrival", de: "Ankunft", fa: "ورود", ar: "الوصول" } }], events: [] },
      audit: { rounds: [], left_out: [], error: null, max_redos: 1 }, created_at: "2026-10-13T08:00:00Z",
    });
    if (path === "/api/documentary/1/stage-control/audit") return json([]);
    if (path.startsWith("/api/documentary/1/stage-control/") && method === "GET" && !path.includes("/result/")) {
      const name = decodeURIComponent(path.split("/stage-control/")[1]);
      return json(state(name, name === "master_story", automaticStages.has(name)));
    }
    if (path === "/api/documentary/1/stage-control/preview" && method === "POST") {
      previewCount += 1;
      const payload = route.request().postDataJSON() as { stage: string; action: string };
      return json({
        case_id: 1, job_id: 12, stage: payload.stage, action: payload.action, prompt: null, edit: {},
        artifact_ids: [201], stage_names: ["spoken:de", "voice:de"], languages: ["de"],
        estimated_scope: [{ id: 201, type: "spoken_version", language: "de", stage: "spoken", will_change: true }, { id: 202, type: "audio_blocks", language: "de", stage: "voice", may_change: true }],
        estimated_provider_calls: { total: 2, by_stage: { "spoken:de": 1, "voice:de": 1 } },
        bound_hashes: { "201": { input_hash: "a", output_hash: "b" } }, preview_id: previewCount,
      });
    }
    if (path === "/api/documentary/1/stage-control/apply" && method === "POST") {
      if (staleApply) return json({ detail: "inputs changed since preview" }, 409);
      resultStatus = "completed";
      return json({ preview_id: 1, job_id: 12, status: "queued", previewed_artifact_ids: [201, 202], executed_artifact_ids: [], changed_outside_scope: [] });
    }
    if (path.match(/\/stage-control\/result\/\d+$/)) return json({ preview_id: 1, case_id: 1, status: "completed", preview: {}, result: { status: resultStatus, previewed_artifact_ids: [201, 202], executed_artifact_ids: [201, 202], executed_stage_names: ["spoken:de", "voice:de"], changed_outside_scope: [] } });
    if (path === "/api/documentary/1/stage-control/automatic" && method === "POST") {
      const payload = route.request().postDataJSON() as { stage: string; enabled: boolean };
      if (payload.enabled) automaticStages.add(payload.stage); else automaticStages.delete(payload.stage);
      return json({ case_id: 1, stage: payload.stage, automatic: payload.enabled, mode: "auto_continue" });
    }
    return json({ detail: "unmocked " + method + " " + path }, 404);
  });
}

async function openFixtureStageControl(page: import("@playwright/test").Page, staleApply = false) {
  await mockStageControlApi(page, staleApply);
  await page.goto("/cases/1");
  await page.getByRole("button", { name: "Pipeline", exact: true }).click();
  await expect(page.getByTestId("stage-control-panel")).toBeVisible();
  await expect(page.getByTestId("stage-card-spoken:de")).toBeVisible();
}

test.describe("stage-control", () => {
  test("fixture-only stale hash state keeps Apply disabled", async ({ page }) => {
    await openFixtureStageControl(page, true);
    const spoken = page.getByTestId("stage-card-spoken:de");
    await spoken.getByRole("button", { name: "Actions" }).click();
    await spoken.getByRole("button", { name: "Edit" }).click();
    await spoken.getByLabel("Chapter title").selectOption({ label: "Ankunft" });
    await spoken.getByLabel("Edit text").fill("A changed German chapter.");
    await spoken.getByRole("button", { name: "Save edit and preview" }).click();
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page.getByTestId("preview-dialog")).toContainText("inputs changed — preview again");
    await expect(page.getByTestId("apply-preview")).toBeDisabled();
  });

  test("real API edit, redo, automatic mode, pending lock, and scope result", async ({ page }) => {
    const apiBase = "http://127.0.0.1:" + (process.env.E2E_API_PORT ?? "8011");
    const beforeResponse = await page.request.get(apiBase + "/api/documentary/1/artifacts");
    expect(beforeResponse.ok()).toBeTruthy();
    const before = (await beforeResponse.json()) as Array<{ language: string | null; output_hash: string | null }>;
    const untouched = Object.fromEntries(before.filter((row) => ["en", "fa", "ar"].includes(row.language ?? "")).map((row) => [row.language, row.output_hash]));

    await page.goto("/cases/1");
    await page.getByRole("button", { name: "Pipeline", exact: true }).click();
    await expect(page.getByTestId("stage-control-panel")).toBeVisible();
    for (const group of ["Story", "Audio", "Visual", "Render", "Shorts"]) {
      await expect(page.getByRole("heading", { name: group, exact: true })).toBeVisible();
    }
    await expect(page.getByText("Preview ID")).toHaveCount(0);
    await expect(page.getByText("outside stage-control preview")).toHaveCount(0);
    const spoken = page.getByTestId("stage-card-spoken:de");
    await spoken.getByRole("button", { name: "Actions" }).click();
    await spoken.getByRole("button", { name: "Edit" }).click();
    await spoken.getByLabel("Chapter title").selectOption({ label: "Ankunft" });
    await spoken.getByLabel("Edit text").fill("A revised German chapter from the real UI.");
    await spoken.getByRole("button", { name: "Save edit and preview" }).click();
    const preview = page.getByTestId("preview-dialog");
    await expect(preview).toBeVisible();
    await expect(preview).toContainText("German");
    await expect(preview).not.toContainText("English");
    await page.screenshot({ path: "../docs/ui/stage-control-preview-desktop.png", fullPage: true });
    await page.getByRole("button", { name: "Apply" }).click();
    const result = page.getByTestId("stage-control-result");
    await expect(result).toContainText("Changed outside scope");
    await expect(result).toContainText("None");
    await page.screenshot({ path: "../docs/ui/stage-control-result-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "../docs/ui/stage-control-result-phone.png", fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });

    const afterResponse = await page.request.get(apiBase + "/api/documentary/1/artifacts");
    const after = (await afterResponse.json()) as Array<{ language: string | null; output_hash: string | null }>;
    expect(Object.fromEntries(after.filter((row) => ["en", "fa", "ar"].includes(row.language ?? "")).map((row) => [row.language, row.output_hash]))).toEqual(untouched);

    // The edit is queued through the German stage.  Exercise redo on the
    // independent completed master stage so the test does not race the
    // worker's legitimate German-stage transition to running.
    const master = page.getByTestId("stage-card-master_story");
    await master.getByRole("button", { name: "Actions" }).click();
    await master.getByRole("button", { name: "Redo with prompt" }).click();
    await master.getByLabel("Prompt").fill("Make the transition more precise.");
    await master.getByRole("button", { name: "Preview" }).click();
    await expect(preview).toContainText("Exact preview scope");
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "../docs/ui/stage-control-preview-phone.png", fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(result).toContainText("None");
    await page.reload();
    await page.getByRole("button", { name: "Pipeline", exact: true }).click();
    await expect(page.getByTestId("stage-control-panel")).toBeVisible();
    await master.getByRole("button", { name: "Actions" }).click();
    await master.getByLabel("Run automatically").click();
    await expect(master.getByLabel("Run automatically")).toBeChecked();
    await page.getByText("Run the whole case automatically").locator("..").locator("..").getByRole("checkbox").check();

    await page.screenshot({ path: "../docs/ui/stage-control-stage-list-desktop.png", fullPage: true });

    const pendingPreview = await page.request.post(apiBase + "/api/documentary/1/stage-control/preview", {
      data: { stage: "spoken:de", action: "redo_with_prompt", prompt: "Leave a pending lock.", job_id: 1 },
    });
    expect(pendingPreview.ok()).toBeTruthy();
    const pendingState = await page.request.get(apiBase + "/api/documentary/1/stage-control/spoken%3Ade?job_id=1");
    expect(pendingState.ok()).toBeTruthy();
    expect((await pendingState.json()).blocked_actions.continue).toBe("pending user edit or lock");
    await page.reload();
    await page.getByRole("button", { name: "Pipeline", exact: true }).click();
    const blockedSpoken = page.getByTestId("stage-card-spoken:de");
    await blockedSpoken.getByRole("button", { name: "Actions" }).click();
    await expect(blockedSpoken.getByText("A pending edit is locking this stage.").first()).toBeVisible();

    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "../docs/ui/stage-control-stage-list-phone.png", fullPage: true });
  });
});
