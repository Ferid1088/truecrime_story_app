// Live proof against the real stack (UI 3001, API 8001, PostgreSQL): the Story tab of case 8.
import { chromium } from "@playwright/test";

const base = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3001";
const out = process.env.SHOT_DIR ?? "../docs/evidence";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 2200 } });
await page.goto(`${base}/cases/8`);
await page.getByRole("button", { name: "Story", exact: true }).click();
await page.getByText("Shared English draft (stage 1, written once)").waitFor({ timeout: 30000 });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${out}/language-pipeline-top.png` });
await page.getByRole("button", { name: /Read the draft/ }).click();
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/language-pipeline-draft.png` });
if (process.env.APPROVE) {
  await page.getByRole("button", { name: "Approve final" }).first().click();
  await page.waitForTimeout(1500);
}
await page.getByText("Cross-language facts check").scrollIntoViewIfNeeded();
await page.screenshot({ path: `${out}/language-pipeline-bottom.png`, fullPage: true });
console.log(await page.locator("body").innerText().then((t) => t.slice(0, 1500)));
await browser.close();
