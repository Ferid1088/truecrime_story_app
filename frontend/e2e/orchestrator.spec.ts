import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const view = {
  worker_alive: true, updated_at: "2026-11-08T12:00:00Z",
  host: { free_gb: 2.1, total_gb: 16, load_per_core: 0.4, cores: 8 },
  host_limits: { min_free_memory_gb: 2, job_memory_gb: 1.5, max_load_per_core: 1.5 },
  running: 4, cap: 9,
  decision: { allowed: false, code: "memory", reason: "2.1 GB memory is free; one more film needs 1.5 GB and 2.0 GB must stay free" },
  providers: {
    llm: { active: 3, configured: 10, limit: 5, throttled: true },
    elevenlabs_tts: { active: 2, configured: 4, limit: 4, throttled: false },
  },
  events: [{ at: 1794161285, code: "memory", reason: "2.1 GB memory is free" }],
  jobs: [
    { id: 1, case_id: 1, case_title: "The Lamp Went Dark", status: "running", languages: ["en"], stage: "voice", progress: 0.4, waiting_because: null },
    { id: 2, case_id: 1, case_title: "The Lamp Went Dark", status: "queued", languages: ["tr"], stage: null, progress: 0, waiting_because: "2.1 GB memory is free; one more film needs 1.5 GB and 2.0 GB must stay free" },
  ],
  runs: [{ id: 9, status: "running", stage: "films", case: { id: 1, title: "The Lamp Went Dark" },
           languages: { en: "completed", fa: "running", tr: null },
           needs_you: [{ key: "k", kind: "film_review", message: "Review and approve the fa film", language: "fa", blocking: true }] }],
};

test.describe("Orchestrator", () => {
  test("shows the machine, the throttled provider, why a film waits and where each run is", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/orchestrator", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(view) }));
    await page.goto("/orchestrator");
    await expect(page.getByTestId("orchestrator-host")).toContainText("2.1 GB of 16.0 GB");
    await expect(page.getByTestId("orchestrator-host")).toContainText("4 of 9");
    await expect(page.getByTestId("orchestrator-decision")).toContainText("memory");
    await expect(page.getByTestId("orchestrator-throttled-llm")).toBeVisible();
    await expect(page.getByTestId("orchestrator-throttled-elevenlabs_tts")).toHaveCount(0);
    await expect(page.getByTestId("orchestrator-jobs")).toContainText("tr · queued · 2.1 GB memory is free");
    await expect(page.getByTestId("orchestrator-runs")).toContainText("fa: running");
    await expect(page.getByTestId("orchestrator-runs")).toContainText("Review and approve the fa film");
    await expect(page.getByTestId("orchestrator-events")).toContainText("memory: 2.1 GB memory is free");
    await expect(page.getByTestId("orchestrator-no-worker")).toHaveCount(0);
  });

  test("says so when no worker is reporting", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/orchestrator", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ ...view, worker_alive: false, host: null, host_limits: null, decision: null, jobs: [], runs: [], events: [], providers: {} }),
    }));
    await page.goto("/orchestrator");
    await expect(page.getByTestId("orchestrator-no-worker")).toContainText("nothing is being started");
  });
});
