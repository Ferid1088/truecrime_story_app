import type { Page, Route } from "@playwright/test";
import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const settings = {
  languages: ["en", "de", "fa", "ar"],
  film_minutes: { min: 45, max: 120 },
  pilot_seconds: 120,
  voices: {},
  channels: { en: { name: "ClueVera" } },
  avatar_generation: { enabled: true },
  pronunciation_check: { enabled: false, languages: [], max_rounds: 1 },
  voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
  rights_profiles: { preview: [], publish: [] },
  dynamic_eq: {},
};
const overview = {
  finals: [], master_version_id: null, film_minutes: { min: 45, max: 120 }, blueprint: null,
  audio_plan: null, languages: {}, visual_plan: null, jobs: [], visual_counts: {},
};
const costs = { case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [] };
const ok = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

const photo = (id: number, situation: string, stance: string, selected = false) =>
  ({ id, situation, stance, status: "available", selected });
const OPTIONS = {
  language: "en", selected: "existing:4", avatar_enabled: true,
  scenes: [{ id: 181, status: "composited", has_video: true, running: false },
           { id: 182, status: "composited", has_video: true, running: false }],
  options: [
    { key: "existing:4", name: "blazer Jeans", ref: "en/blazerJeans", source: "case", status: "selected", selected: true,
      photos: [photo(15, "sitHands", "sitting", true), photo(16, "standHand", "standing")] },
    { key: "registry:en/navyCordBlazerVest", name: "navy Cord Blazer Vest", ref: "en/navyCordBlazerVest",
      source: "library", status: "available", selected: false,
      photos: [photo(142, "front", "unknown"), photo(144, "sitCrossed", "sitting"), photo(145, "sitHands", "sitting")] },
  ],
};

async function mockPage(page: Page) {
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
  await page.route("**/api/host-scenes**", (r: Route) => r.fulfill(ok([])));
  await page.route("**/api/outfit-library/items/*/image", (r: Route) =>
    r.fulfill({ status: 200, contentType: "image/png", body: PNG }));
}

test.describe("host window: choose the host's outfit and one of its photos", () => {
  test("pick an outfit, pick a photo, the app asks, and only a yes saves it and creates the host", async ({ page }) => {
    await mockPage(page);
    await page.route("**/api/cases/1/host-outfit?**", (r: Route) => r.fulfill(ok(OPTIONS)));
    const saved: unknown[] = [];
    await page.route("**/api/cases/1/host-outfit", (r: Route) => {
      if (r.request().method() !== "POST") return r.fallback();
      saved.push(r.request().postDataJSON());
      return r.fulfill(ok({ outfit: { id: 9, name: "navy Cord Blazer Vest", status: "selected" },
        attached: [{ look_id: 1, photo: "x", stance: "SEATED" }], skipped: [], started: [181, 182], kept: [] }));
    });
    await page.goto("/documentary/1");
    await page.getByRole("tab", { name: "Host" }).click();
    const panel = page.getByTestId("host-outfit-panel");
    await expect(panel).toContainText("blazer Jeans");
    await panel.getByRole("button", { name: "Change outfit" }).click();
    await panel.getByRole("radio", { name: "navy Cord Blazer Vest" }).click();
    await expect(panel.getByRole("radio", { name: "front" })).toBeDisabled();            // no recognised pose
    await expect(panel.getByRole("button", { name: "Use this outfit and photo" })).toBeDisabled();
    await panel.getByRole("radio", { name: "sitHands" }).click();
    await panel.getByRole("button", { name: "Use this outfit and photo" }).click();

    const dialog = page.getByRole("dialog", { name: "Are you sure?" });
    await expect(dialog).toContainText("navy Cord Blazer Vest");
    await expect(dialog).toContainText("2 host videos");
    expect(saved).toHaveLength(0);                                                       // nothing is saved yet
    await dialog.getByRole("button", { name: "No, go back" }).click();
    expect(saved).toHaveLength(0);

    await panel.getByRole("button", { name: "Use this outfit and photo" }).click();
    await page.getByRole("dialog", { name: "Are you sure?" }).getByTestId("host-outfit-confirm").click();
    await expect.poll(() => saved[0]).toEqual({
      language: "en", option_key: "registry:en/navyCordBlazerVest", item_id: 145, create_host: true });
    await expect(panel).toContainText("Saved: navy Cord Blazer Vest");
    await expect(panel).toContainText("2 host videos are being made");
  });

  test("the owner can save the choice without creating the host", async ({ page }) => {
    await mockPage(page);
    await page.route("**/api/cases/1/host-outfit?**", (r: Route) => r.fulfill(ok(OPTIONS)));
    const saved: Record<string, unknown>[] = [];
    await page.route("**/api/cases/1/host-outfit", (r: Route) => {
      if (r.request().method() !== "POST") return r.fallback();
      saved.push(r.request().postDataJSON());
      return r.fulfill(ok({ outfit: { id: 9, name: "blazer Jeans", status: "selected" },
        attached: [], skipped: [], started: [], kept: [] }));
    });
    await page.goto("/documentary/1");
    await page.getByRole("tab", { name: "Host" }).click();
    const panel = page.getByTestId("host-outfit-panel");
    await panel.getByRole("button", { name: "Change outfit" }).click();
    await panel.getByRole("radio", { name: "blazer Jeans (now)" }).click();
    await panel.getByRole("radio", { name: "standHand" }).click();
    await panel.getByRole("button", { name: "Use this outfit and photo" }).click();
    const dialog = page.getByRole("dialog", { name: "Are you sure?" });
    await dialog.getByRole("checkbox").uncheck();
    await dialog.getByTestId("host-outfit-confirm").click();
    await expect.poll(() => saved[0]?.create_host).toBe(false);
  });

  test("a refused choice shows the reason and keeps the dialog's work", async ({ page }) => {
    await mockPage(page);
    await page.route("**/api/cases/1/host-outfit?**", (r: Route) => r.fulfill(ok(OPTIONS)));
    await page.route("**/api/cases/1/host-outfit", (r: Route) => {
      if (r.request().method() !== "POST") return r.fallback();
      return r.fulfill({ status: 409, contentType: "application/json",
        body: JSON.stringify({ detail: "one or more selected photos do not belong to this outfit" }) });
    });
    await page.goto("/documentary/1");
    await page.getByRole("tab", { name: "Host" }).click();
    const panel = page.getByTestId("host-outfit-panel");
    await panel.getByRole("button", { name: "Change outfit" }).click();
    await panel.getByRole("radio", { name: "navy Cord Blazer Vest" }).click();
    await panel.getByRole("radio", { name: "sitCrossed" }).click();
    await panel.getByRole("button", { name: "Use this outfit and photo" }).click();
    await page.getByTestId("host-outfit-confirm").click();
    await expect(panel.getByRole("alert")).toContainText("do not belong to this outfit");
  });
});
