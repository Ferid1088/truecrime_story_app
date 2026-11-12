import type { Route } from "@playwright/test";
import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const settings = {
  languages: ["en", "fa"], film_minutes: { min: 45, max: 120 }, pilot_seconds: 120, voices: {}, channels: { en: { name: "ClueVera" } }, avatar_generation: { enabled: true },
  voice_performance: { enabled: true, level_tags: {}, level_styles: {} },
  concurrency: { llm: 1, vision: 1, elevenlabs_tts: 1, elevenlabs_sound: 1, asr: 1, render: 1, downloads: 1, jobs: 1, languages: 1 },
  render: { width: 1920, height: 1080, fps: 24, audio_sample_rate: 48000, burn_subtitles: true, subtitle_max_chars: 42, subtitle_max_seconds: 7, show_credits: true },
  rights_profiles: { preview: [], publish: [] }, dynamic_eq: {},
};
const overview = { finals: [], master_version_id: null, film_minutes: { min: 45, max: 120 }, blueprint: null, audio_plan: null, languages: { en: { version_id: 7 } }, visual_plan: null, jobs: [], visual_counts: {} };
const costs = { case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [] };
const ok = (body: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
const PIXEL = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAHgyoKQAAAABJRU5ErkJggg==", "base64");

type Img = { id: number; url: string; attempt: number; cost_usd: number; cost_eur: number; duration_s: number; image_tokens_out: number };
type Cover = { id: number; key: string; kind: "long" | "short"; version: number; source: "agent" | "owner"; status: string; title: string; scene_prompt: string; image: Img | null;
               confidence: number | null; reason: string; validation: { issues: never[] }; preview_url: string | null; approved_url: string | null };

const SCENE = "A front door standing slightly open at night, a narrow strip of warm light on the dark floor";
const coverCosts = (pictures: number) => ({ model: "gpt-image-2.6-flare", attempts: pictures, pictures, failed: 0, image_tokens_out: pictures * 1584, text_tokens_in: pictures * 140, cost_usd: pictures * 0.04752, cost_eur: pictures * 0.0425,
                                       seconds: pictures * 31, estimated: true, rate_status: "unverified",
                                       rows: Array.from({ length: pictures }, (_, i) => ({ id: i + 1, cover_key: "C1", attempt: 1, ok: true, error: null, text_tokens_in: 140, image_tokens_in: 0, text_tokens_out: 0, image_tokens_out: 1584,
                                                                                         cost_usd: 0.04752, cost_eur: 0.0425, cost_source: "assumed-unverified", duration_s: 31 })) });

function cover(over: Partial<Cover> & { key: string }): Cover {
  return { id: 1, kind: over.key === "long" ? "long" : "short", version: 1, source: "agent", status: "proposed", title: "The open door", scene_prompt: SCENE, image: null, confidence: 0.9,
           reason: "a front door, as the title says", validation: { issues: [] }, preview_url: null, approved_url: null, ...over };
}

async function mockCovers(page: import("@playwright/test").Page, covers: Cover[]) {
  const calls: string[] = [];
  const counts = () => ({ expected_per_language: 6, languages: { en: { proposed: covers.filter((c) => c.status === "proposed").length, open: covers.filter((c) => c.status === "open").length,
                          approved: covers.filter((c) => c.status === "approved").length, skipped: covers.filter((c) => c.status === "skipped").length, missing: 6 - covers.length }, fa: { proposed: 0, open: 0, approved: 0, skipped: 0, missing: 6 } } });
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) => r.fulfill(ok(settings)));
  await page.route("**/api/cases/1/documentary", (r: Route) => r.fulfill(ok(overview)));
  await page.route("**/api/cases/1/costs", (r: Route) => r.fulfill(ok(costs)));
  await page.route("**/api/cases/1/covers/summary", (r: Route) => r.fulfill(ok(counts())));
  await page.route("**/api/cases/1/covers/en", (r: Route) => r.fulfill(ok({ case_id: 1, language: "en", look: "midnight navy", expected: 6, covers, costs: coverCosts(covers.filter((c) => c.image).length),
                                                                          platform_cover_ways: { instagram_reel: "to_test" }, safe_zones: "to_test" })));
  await page.route("**/api/cases/1/covers/en/*/image*", (r: Route) => r.request().method() === "GET" ? r.fulfill({ status: 200, contentType: "image/png", body: PIXEL }) : r.fallback());
  await page.route("**/api/cases/1/covers/en/*/preview*", (r: Route) => r.request().method() === "GET" ? r.fulfill({ status: 200, contentType: "image/png", body: PIXEL }) : r.fallback());
  await page.route("**/api/cases/1/covers/en/*/file*", (r: Route) => r.fulfill({ status: 200, contentType: "image/png", body: PIXEL }));
  const replace = (key: string, patch: Partial<Cover>) => {
    const i = covers.findIndex((c) => c.key === key);
    covers[i] = { ...covers[i], ...patch };
    return covers[i];
  };
  await page.route("**/api/cases/1/covers/en/C1", async (r: Route) => {
    const body = r.request().postDataJSON() as { title?: string; scene?: string };
    calls.push(`change ${JSON.stringify(body)}`);
    const sceneChanged = body.scene != null && body.scene !== covers[1].scene_prompt;
    await r.fulfill(ok(replace("C1", { version: covers[1].version + 1, source: "owner", title: body.title ?? covers[1].title, scene_prompt: body.scene ?? covers[1].scene_prompt,
                                       image: sceneChanged ? null : covers[1].image, status: "proposed", preview_url: null, approved_url: null, confidence: null })));
  });
  await page.route("**/api/cases/1/covers/en/C1/image", async (r: Route) => {
    calls.push(`image ${r.request().postData()}`);
    await r.fulfill(ok(replace("C1", { image: { id: 5, url: "/api/cases/1/covers/en/C1/image?image=5", attempt: 1, cost_usd: 0.04752, cost_eur: 0.0425, duration_s: 31.2, image_tokens_out: 1584 } })));
  });
  await page.route("**/api/cases/1/covers/en/C1/preview", async (r: Route) => {
    if (r.request().method() !== "POST") return r.fallback();
    calls.push("preview");
    return r.fulfill(ok({ ...replace("C1", { preview_url: "/api/cases/1/covers/en/C1/preview?row=1" }), problems: [] }));
  });
  await page.route("**/api/cases/1/covers/en/C1/approve", async (r: Route) => { calls.push("approve"); await r.fulfill(ok(replace("C1", { status: "approved", approved_url: "/api/cases/1/covers/en/C1/file?row=1" }))); });
  await page.route("**/api/cases/1/covers/en/C1/skip", async (r: Route) => { calls.push("skip"); await r.fulfill(ok(replace("C1", { status: "skipped" }))); });
  return calls;
}

const SHORTS_TAB = "/documentary/1?tab=Short%20clips%20%26%20covers";

test("the Film cover tab sits right after Host and shows only the film's cover; the short covers are under Short clips & covers", async ({ page }) => {
  await mockCovers(page, [cover({ key: "long", id: 1 }), cover({ key: "C1", id: 2, title: "Who opened it" })]);
  await page.goto("/documentary/1");
  await expect(page.getByRole("tab", { name: "Host" })).toBeVisible();
  const tabs = await page.getByRole("tab").allInnerTexts();
  expect(tabs.indexOf("Film cover")).toBe(tabs.indexOf("Host") + 1);
  expect(tabs).not.toContain("Covers");
  await page.getByRole("tab", { name: "Film cover" }).click();
  await expect(page.getByTestId("cover-long")).toContainText("Film cover (16:9)");
  await expect(page.getByTestId("cover-C1")).toHaveCount(0);
  await page.getByRole("tab", { name: "Short clips & covers" }).click();
  await page.getByTestId("shorts-view-covers").click();
  await expect(page.getByTestId("cover-C1")).toContainText("Short C1 cover (9:16)");
  await expect(page.getByTestId("cover-long")).toHaveCount(0);
});

test("the Short covers view shows the short covers with their scene descriptions, counts and the cost book; nothing is approved by the planner", async ({ page }) => {
  await mockCovers(page, [cover({ key: "long", id: 1 }), cover({ key: "C1", id: 2, title: "Who opened it" }), cover({ key: "C2", id: 3, status: "open", confidence: 0.4 })]);
  await page.goto(SHORTS_TAB);
  await page.getByTestId("shorts-view-covers").click();
  await expect(page.getByTestId("covers-counts")).toContainText("0 approved, 2 proposed, 1 open, 0 skipped, 3 not planned yet (of 6)");
  await expect(page.getByTestId("cover-C1")).toContainText("Short C1 cover (9:16)");
  await expect(page.getByTestId("cover-C2-status")).toHaveText("open");
  await expect(page.getByTestId("cover-C1").getByLabel("Scene of Short C1 cover (9:16)")).toHaveValue(SCENE);
  await expect(page.getByTestId("cover-costs")).toContainText("Cost book: 0 pictures");
  await expect(page.getByText("still marked \"to test\"")).toBeVisible();
  await expect(page.getByTestId("cover-C1").getByRole("button", { name: "Approve" })).toBeDisabled();       // no picture and no preview yet
  await expect(page.getByTestId("cover-C1").getByRole("button", { name: "Preview" })).toBeDisabled();
});

test("a title and a scene are changed, the picture is made on request with its cost, the preview comes first, then the cover is approved; a change takes the approval away", async ({ page }) => {
  const calls = await mockCovers(page, [cover({ key: "long", id: 1 }), cover({ key: "C1", id: 2, title: "Who opened it" })]);
  await page.goto(SHORTS_TAB);
  await page.getByTestId("shorts-view-covers").click();
  const card = page.getByTestId("cover-C1");
  await card.getByLabel("Title of Short C1 cover (9:16)").fill("Why it stayed open");
  await card.getByRole("button", { name: "Save title" }).click();
  await expect(card).toContainText("your choice · v2");
  await card.getByRole("button", { name: "Make the picture (paid)" }).click();
  await expect(card.getByTestId("cover-C1-cost")).toContainText("$0.0475 estimated · 1584 image tokens · 31.2 s · attempt 1");
  await expect(page.getByTestId("cover-costs")).toContainText("Cost book: 1 pictures, $0.0475");
  await expect(card.getByRole("button", { name: "Approve" })).toBeDisabled();                               // the preview comes first
  await card.getByRole("button", { name: "Preview" }).click();
  await expect(card.getByRole("img", { name: "Preview of Short C1 cover (9:16)" })).toBeVisible();
  await card.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByTestId("cover-C1-status")).toHaveText("approved");
  await expect(page.getByTestId("covers-counts")).toContainText("1 approved");
  await page.getByTestId("cover-C1").getByLabel("Scene of Short C1 cover (9:16)").fill("A garage door raised halfway on a snowy night, warm light spilling out");
  await page.getByTestId("cover-C1").getByRole("button", { name: "Save scene" }).click();                    // a change takes the approval and the picture away
  await expect(page.getByTestId("cover-C1-status")).toHaveText("proposed");
  await expect(page.getByTestId("cover-C1").getByRole("button", { name: "Make the picture (paid)" })).toBeVisible();
  await expect(page.getByTestId("cover-C1").getByRole("button", { name: "Approve" })).toBeDisabled();
  await page.getByTestId("cover-C1").getByRole("button", { name: "Skip" }).click();
  await expect(page.getByTestId("cover-C1-status")).toHaveText("skipped");
  expect(calls.indexOf("preview")).toBeLessThan(calls.indexOf("approve"));
  expect(calls.filter((c) => c.startsWith("image"))).toEqual(['image {"again":false}']);
});
