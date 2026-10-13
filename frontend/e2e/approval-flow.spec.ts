import { expect, test } from "@playwright/test";

const apiBase = process.env.PLAYWRIGHT_API_BASE_URL ?? "http://127.0.0.1:8011";

test.beforeEach(async ({ request }) => {
  const response = await request.post(`${apiBase}/api/e2e/reset-approval-flow`);
  expect(response.ok()).toBeTruthy();
  expect((await response.json()).database).toBe("truecrime_e2e");
});

test("recorded naming succeeds, thumbnail approval applies, and localization approval applies", async ({ page }) => {
  await page.goto("/cases/1");
  await page.getByRole("button", { name: "Naming", exact: true }).click();
  await page.getByRole("button", { name: "Generate", exact: true }).first().click();
  await expect(page.getByText("The Harbor Ledger 1").first()).toBeVisible({ timeout: 20_000 });

  await page.getByRole("button", { name: "Thumbnail", exact: true }).click();
  await page.getByRole("button", { name: "Approve", exact: true }).first().click();
  await page.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(page.getByText(/Applied; changed outside scope: none/)).toBeVisible();

  await page.getByRole("button", { name: "Localizations", exact: true }).click();
  await expect(page.getByText("ready for human review")).toBeVisible();
  await page.getByRole("button", { name: "Approve localization", exact: true }).click();
  await page.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(page.getByText(/Applied; changed outside scope: none/)).toBeVisible();
});

test("production Shorts worker renders a 1080x1920 captioned Persian-safe package", async ({ page, request }) => {
  test.setTimeout(120_000);
  const reset = await request.post(`${apiBase}/api/e2e/reset-shorts-flow`);
  expect(reset.ok()).toBeTruthy();
  for (const language of ["en", "fa", "ar"]) {
    const created = await request.post(`${apiBase}/api/short-form/1/create`, { data: { language } });
    expect(created.ok()).toBeTruthy();
  }
  const deadline = Date.now() + 90_000;
  let done: { id: number; status: string; language: string; stage: string }[] = [];
  while (Date.now() < deadline) {
    const rows = await (await request.get(`${apiBase}/api/short-form/1/queue`)).json() as { id: number; status: string; language: string; stage: string }[];
    const scriptRows = rows.filter((row) => row.stage === "script_render" && ["en", "fa", "ar"].includes(row.language));
    done = scriptRows.filter((row) => row.status === "done");
    if (scriptRows.length > 0 && scriptRows.every((row) => row.status === "done") && new Set(done.map((row) => row.language)).size === 3) break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  expect([...new Set(done.map((row) => row.language))].sort()).toEqual(["ar", "en", "fa"]);
  await page.goto("/short-form");
  await page.getByRole("tab", { name: "Mock run" }).click();
  await expect(page.getByText("Real renders", { exact: true })).toBeVisible({ timeout: 20_000 });
  const workspace = await (await request.get(`${apiBase}/api/short-form/1/workspace`)).json() as { renders: { language: string; video_url: string | null; captions_url: string | null; render_source: string }[] };
  for (const language of ["en", "fa", "ar"]) {
    const render = workspace.renders.find((row) => row.language === language && row.render_source === "real");
    expect(render?.video_url).toBeTruthy();
    expect(render?.captions_url).toBeTruthy();
    const manifest = await (await request.get(`${apiBase}${render!.video_url!.replace(/\/video$/, "/manifest")}`)).json() as { width: number; height: number; captions_burned_in: boolean; subtitle_layout?: { direction?: string } };
    expect(manifest.width).toBe(1080);
    expect(manifest.height).toBe(1920);
    expect(manifest.captions_burned_in).toBe(true);
    if (["fa", "ar"].includes(language)) expect(manifest.subtitle_layout?.direction).toBe("rtl");
  }
});

test("Films approval bundle approves, excludes, and invalidates only an edited short", async ({ page, request }) => {
  test.setTimeout(120_000);
  const reset = await request.post(`${apiBase}/api/e2e/reset-shorts-flow`);
  expect(reset.ok()).toBeTruthy();
  const created = await request.post(`${apiBase}/api/short-form/1/create`, { data: { language: "en" } });
  expect(created.ok()).toBeTruthy();
  const deadline = Date.now() + 90_000;
  let workspace: { renders: { id: number; language: string; render_source: string }[] } | null = null;
  while (Date.now() < deadline) {
    const rows = await (await request.get(`${apiBase}/api/short-form/1/queue`)).json() as { stage: string; status: string }[];
    if (rows.some((row) => row.stage === "script_render") && rows.filter((row) => row.stage === "script_render").every((row) => row.status === "done")) {
      workspace = await (await request.get(`${apiBase}/api/short-form/1/workspace`)).json() as typeof workspace;
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  expect(workspace?.renders.length).toBeGreaterThanOrEqual(3);
  const renders = workspace!.renders.filter((render) => render.language === "en");
  await page.goto("/cases/1");
  await page.getByRole("button", { name: "Films", exact: true }).click();
  await expect(page.getByText("Approval bundles", { exact: true })).toBeVisible();
  const approveButtons = page.getByRole("button", { name: "Approve", exact: true });
  await approveButtons.nth(0).click();
  await expect(page.getByText("approved", { exact: true }).first()).toBeVisible();
  await approveButtons.nth(0).click();
  await page.getByRole("button", { name: "Exclude", exact: true }).first().click();

  const approveShort = await request.post(`${apiBase}/api/cases/1/approval-bundles/en/items/short/${renders[0].id}/approve`);
  expect(approveShort.ok()).toBeTruthy();
  const excludeShort = await request.post(`${apiBase}/api/cases/1/approval-bundles/en/items/short/${renders[1].id}/exclude`);
  expect(excludeShort.ok()).toBeTruthy();

  const preview = await request.post(`${apiBase}/api/short-form/1/renders/${renders[0].id}/preview`, {
    data: { action: "edit", edit: { caption: "Edited bundle caption." } },
  });
  expect(preview.ok()).toBeTruthy();
  const previewBody = await preview.json() as { preview_id: number };
  const applied = await request.post(`${apiBase}/api/short-form/1/operations/${previewBody.preview_id}/apply`);
  expect(applied.ok()).toBeTruthy();
  const bundle = await (await request.get(`${apiBase}/api/cases/1/approval-bundles/en`)).json() as { items: { item_type: string; item_id: number; status: string }[] };
  expect(bundle.items.find((item) => item.item_type === "short" && item.item_id === renders[0].id)?.status).toBe("needs_review");
  expect(bundle.items.filter((item) => item.status === "excluded").length).toBe(1);
});

test("phone viewport has no horizontal overflow on the main screens", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of [
    "/", "/discover", "/cases", "/research", "/studio", "/documentary",
    "/short-form", "/archive", "/database", "/ops", "/settings",
    "/cases/1", "/documentary/1",
  ]) {
    await page.goto(path);
    await expect(page.locator(".animate-pulse")).toHaveCount(0, { timeout: 20_000 });
    const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, viewport: window.innerWidth }));
    expect(widths.scroll, `${path}: ${widths.scroll}px > ${widths.viewport}px`).toBeLessThanOrEqual(widths.viewport);
  }
});
