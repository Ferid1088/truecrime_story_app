import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";
import type { Page, Route } from "@playwright/test";

const PIXEL = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

const settings = {
  languages: ["en", "de", "fa", "ar"],
  film_minutes: { min: 45, max: 120 },
  pilot_seconds: 120,
  voices: {},
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

function look(id: number, pose = "P01_NEUTRAL_SEATED") {
  return { id, stance: "SEATED", camera: "CAM01_FRONT", framing: "MEDIUM", pose, has_image: true };
}

/** The live panel state the mocked endpoints keep and return. */
function makePanel() {
  const panel = {
    case_id: 1,
    languages: [
      {
        language: "en", chosen_outfit_id: 11, fixed_look_id: null, final_rendered: false,
        outfits: [
          { id: 11, name: "Navy suit", status: "selected", looks: [look(101), look(102, "P02_SLIGHT_FORWARD")], to_approve: [] },
          { id: 12, name: "Grey", status: "draft", looks: [], to_approve: ["request front, medium, seated"] },
        ],
      },
      {
        language: "de", chosen_outfit_id: null, fixed_look_id: null, final_rendered: true,
        outfits: [{ id: 13, name: "Navy suit", status: "draft", looks: [look(103)], to_approve: [] }],
      },
      { language: "fa", chosen_outfit_id: null, fixed_look_id: null, final_rendered: false, outfits: [{ id: 14, name: "Grey", status: "draft", looks: [], to_approve: [] }] },
      { language: "ar", chosen_outfit_id: null, fixed_look_id: null, final_rendered: false, outfits: [] },
    ],
    name_in_languages: { "navy suit": ["en", "de"], grey: ["en", "fa"] },
  };
  return panel;
}

async function mockPage(page: Page) {
  const panel = makePanel();
  await mockApi(page);
  await page.route("**/api/documentary/settings", (r: Route) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(settings) }));
  await page.route("**/api/cases/1/documentary", (r: Route) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(overview) }));
  await page.route("**/api/cases/1/costs", (r: Route) =>
    r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ case_id: 1, cap_eur: 300, spent_eur: 0, remaining_eur: 300, status: "new", stop_reason: null, calls: [] }) }));
  await page.route("**/api/host-looks/*/image", (r: Route) =>
    r.fulfill({ status: 200, contentType: "image/png", body: PIXEL }));
  await page.route("**/api/cases/1/outfit-panel", (r: Route) => {
    if (r.request().method() === "GET")
      return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(panel) });
    return r.continue();
  });
  await page.route("**/api/cases/1/outfit-panel/select", (r: Route) => {
    const id = (r.request().postDataJSON() as { outfit_id: number }).outfit_id;
    for (const lang of panel.languages)
      if (lang.outfits.some((o) => o.id === id)) {
        lang.chosen_outfit_id = id;
        for (const o of lang.outfits)
          o.status = o.id === id ? "selected" : o.status === "selected" ? "draft" : o.status;
      }
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(panel) });
  });
  await page.route("**/api/cases/1/outfit-panel/same-outfit", (r: Route) => {
    const id = (r.request().postDataJSON() as { outfit_id: number }).outfit_id;
    const src = panel.languages.flatMap((l) => l.outfits).find((o) => o.id === id)!;
    const applied: string[] = [], skipped: string[] = [];
    for (const lang of panel.languages) {
      if (lang.language === srcLang(panel, id)) continue;
      const twin = lang.outfits.find((o) => o.name === src.name);
      if (!twin) { skipped.push(lang.language); continue; }
      lang.chosen_outfit_id = twin.id;
      twin.status = "selected";
      applied.push(lang.language);
    }
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...panel, result: { applied, skipped } }) });
  });
  await page.route("**/api/cases/1/outfit-panel/fixed-look", (r: Route) => {
    const body = r.request().postDataJSON() as { outfit_id: number; look_id: number | null };
    const lang = panel.languages.find((l) => l.outfits.some((o) => o.id === body.outfit_id))!;
    lang.fixed_look_id = body.look_id;
    return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(panel) });
  });
  return panel;
}

function srcLang(panel: ReturnType<typeof makePanel>, outfitId: number): string {
  return panel.languages.find((l) => l.outfits.some((o) => o.id === outfitId))!.language;
}

test.describe("Outfits panel", () => {
  test("opens from the button next to Start and shows one row per language", async ({ page }) => {
    await mockPage(page);
    await page.goto("/documentary/1");
    await page.getByRole("button", { name: "Outfits" }).click();
    const dialog = page.getByRole("dialog", { name: "Outfits" });
    await expect(dialog).toBeVisible();
    for (const lang of ["en", "de", "fa", "ar"])
      await expect(dialog.getByTestId(`outfit-row-${lang}`)).toBeVisible();
    // small pictures of the approved looks are listed next to each outfit
    await expect(
      dialog.getByTestId("outfit-row-en").getByRole("radiogroup", { name: "English outfit" }).locator("img"),
    ).toHaveCount(2);
    // the rendered German film warns that a change needs a new render
    await expect(dialog.getByTestId("outfit-row-de")).toContainText("needs a new render");
    // Arabic has no outfits yet
    await expect(dialog.getByTestId("outfit-row-ar")).toContainText("No outfits in this studio yet");
  });

  test("choose per language; 'same outfit' copies by name and lists skipped", async ({ page }) => {
    await mockPage(page);
    await page.goto("/documentary/1");
    await page.getByRole("button", { name: "Outfits" }).click();
    const dialog = page.getByRole("dialog", { name: "Outfits" });

    // choose "Navy suit" for German, then copy it to every language that has it
    const de = dialog.getByTestId("outfit-row-de");
    await de.getByRole("radio", { name: /Navy suit/ }).click();
    await expect(de.getByRole("radio", { name: /Navy suit/ })).toBeChecked();
    const sameOutfit = de.getByRole("checkbox", { name: "Same outfit for all languages" });
    await expect(sameOutfit).toBeEnabled();
    await sameOutfit.click();
    await expect(sameOutfit).toBeChecked();
    await expect(dialog.getByRole("status")).toContainText("skipped: Persian, Arabic");

    // "same picture" appears only while "same outfit" is ticked
    await expect(de.getByRole("checkbox", { name: "Same picture for all languages" })).toBeVisible();
    await sameOutfit.click();
    await expect(de.getByRole("checkbox", { name: "Same picture for all languages" })).toBeHidden();
  });

  test("same outfit is disabled with the reason when no twin name exists", async ({ page }) => {
    const panel = await mockPage(page);
    panel.languages[1].outfits[0].name = "Nur DE";          // de outfit renamed: no twin of "Navy suit"…
    panel.languages[0].outfits[0].name = "Only English";    // …and vice versa
    panel.name_in_languages = { "only english": ["en"], "nur de": ["de"], grey: ["en", "fa"] };
    await page.goto("/documentary/1");
    await page.getByRole("button", { name: "Outfits" }).click();
    const en = page.getByRole("dialog", { name: "Outfits" }).getByTestId("outfit-row-en");
    const box = en.getByRole("checkbox", { name: "Same outfit for all languages" });
    await expect(box).toBeDisabled();
    await expect(en).toContainText("no other language has an outfit with this name");
  });

  test("one fixed picture: picking a thumbnail pins it for every appearance", async ({ page }) => {
    await mockPage(page);
    await page.goto("/documentary/1");
    await page.getByRole("button", { name: "Outfits" }).click();
    const en = page.getByRole("dialog", { name: "Outfits" }).getByTestId("outfit-row-en");
    await en.getByTestId("fixed-look-102").click();
    await expect(en.getByRole("checkbox", { name: "One fixed picture" })).toBeChecked();
    await expect(en).toContainText("Picture #102 is used for every appearance");
    // unticking clears it again (rotation back on)
    await en.getByRole("checkbox", { name: "One fixed picture" }).click();
    await expect(en.getByRole("checkbox", { name: "One fixed picture" })).not.toBeChecked();
    await expect(en).toContainText("Pick a picture to use it for every appearance");
  });
});
