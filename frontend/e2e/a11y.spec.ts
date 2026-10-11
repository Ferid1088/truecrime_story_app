import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

// Serious and critical findings fail the build; minor ones are left to design review.
const PAGES = ["/", "/ops", "/cases", "/discover", "/settings"];

for (const path of PAGES) {
  test(`no serious accessibility violations on ${path}`, async ({ page }) => {
    await mockApi(page);
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(bad.map((v) => `${v.id}: ${v.nodes.length} node(s) — ${v.help}`)).toEqual([]);
  });
}

test("login page has no serious accessibility violations", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/auth/session", (r) =>
    r.fulfill({ contentType: "application/json", body: '{"authenticated":false,"auth_required":true}' }),
  );
  await page.goto("/login");
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
});
