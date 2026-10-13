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

const overview = {
  masters: [{ id: 41, version: 1, language: "en", status: "approved", words: 900, kind: "master" }],
  master_version_id: 41,
  film_minutes: { min: 45, max: 120 },
  blueprint: null,
  audio_plan: null,
  languages: { en: null, de: null, fa: null, ar: null },
  visual_plan: null,
  jobs: [{
    id: 12, case_id: 1, master_version_id: 41, mode: "full", languages: ["en", "de", "fa", "ar"],
    pilot_seconds: null, render_profile: "preview", refresh_visuals: false, from_zero: false,
    target_minutes: null, batch_id: null, production_type: "new", follow_up_id: null,
    status: "completed", stage: null, progress: 1, stages, result: {}, error: null,
    created_at: "2026-10-13T08:00:00Z", updated_at: "2026-10-13T08:01:00Z", completed_at: "2026-10-13T08:01:00Z",
  }],
  visual_counts: {},
};

const settings = {
  languages: ["en", "de", "fa", "ar"], film_minutes: { min: 45, max: 120 }, pilot_seconds: 60,
  voices: {}, pronunciation_check: { enabled: false, languages: [], max_rounds: 1 },
  voice_performance: { enabled: false, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  styles: {}, render: { profile: "preview", width: 1280, height: 720, fps: 25, crf: 23, preset: "fast", burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 6, show_credits: true },
  rights_profiles: {}, dynamic_eq: {},
};

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
    if (path === "/api/documentary/settings") return json(settings);
    if (path === "/api/cases/1/documentary") return json(overview);
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
        estimated_scope: [{ id: 201, type: "spoken_version", language: "de", stage: "spoken" }, { id: 202, type: "audio_blocks", language: "de", stage: "voice", may_change: true }],
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
    if (path === "/api/documentary/jobs/12") return json(overview.jobs[0]);
    return json({ detail: `unmocked ${method} ${path}` }, 404);
  });
}

async function openStageControl(page: import("@playwright/test").Page, staleApply = false) {
  await mockStageControlApi(page, staleApply);
  await page.goto("/documentary/1");
  await page.getByRole("tab", { name: "Stage Control" }).click();
  await expect(page.getByTestId("stage-control-panel")).toBeVisible();
  await expect(page.getByTestId("stage-card-spoken:de")).toBeVisible();
}

test.describe("stage-control components", () => {
  test("StageCard, ActionPanel and PreviewDialog cover edit, blocked and may-change states", async ({ page }) => {
    await openStageControl(page);
    const spoken = page.getByTestId("stage-card-spoken:de");
    await expect(spoken.getByRole("button", { name: "Edit" })).toBeVisible();
    await spoken.getByRole("button", { name: "Edit" }).click();
    await spoken.getByLabel("Edit text").fill("A changed German chapter.");
    await spoken.getByRole("button", { name: "Save edit and preview" }).click();
    await expect(page.getByTestId("preview-dialog")).toContainText("German");
    await expect(page.getByTestId("preview-dialog")).toContainText("spoken_version");
    await expect(page.getByTestId("preview-dialog")).toContainText("may change");
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByTestId("stage-card-master_story").getByText("pending user edit or lock").first()).toBeVisible();
  });

  test("German edit apply, redo-with-prompt and automatic mode are visible and scoped", async ({ page }) => {
    await openStageControl(page);
    const spoken = page.getByTestId("stage-card-spoken:de");
    await spoken.getByRole("button", { name: "Edit" }).click();
    await spoken.getByLabel("Edit text").fill("A changed German chapter.");
    await spoken.getByRole("button", { name: "Save edit and preview" }).click();
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page.getByTestId("stage-control-result")).toContainText("Changed outside scope");
    await expect(page.getByTestId("stage-control-result")).toContainText("None");
    await spoken.getByRole("button", { name: "Redo with prompt" }).click();
    await spoken.getByLabel("Prompt").fill("Make the transition more precise.");
    await spoken.getByRole("button", { name: "Preview" }).click();
    await expect(page.getByTestId("preview-dialog")).toContainText("German");
    await page.getByRole("button", { name: "Cancel" }).click();
    await spoken.getByLabel("Run automatically").click();
    await expect(spoken.getByLabel("Run automatically")).toBeChecked();
  });

  test("stale hash keeps Apply disabled and asks for a fresh preview", async ({ page }) => {
    await openStageControl(page, true);
    const spoken = page.getByTestId("stage-card-spoken:de");
    await spoken.getByRole("button", { name: "Edit" }).click();
    await spoken.getByLabel("Edit text").fill("A changed German chapter.");
    await spoken.getByRole("button", { name: "Save edit and preview" }).click();
    await page.getByRole("button", { name: "Apply" }).click();
    await expect(page.getByTestId("preview-dialog")).toContainText("inputs changed — preview again");
    await expect(page.getByTestId("apply-preview")).toBeDisabled();
  });

  test("responsive screenshots document the desktop and phone states", async ({ page }) => {
    await openStageControl(page);
    await page.screenshot({ path: "../docs/ui/stage-control-desktop.png", fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: "../docs/ui/stage-control-phone.png", fullPage: true });
  });
});
