// Live proof: the three checks of the English draft on the Story tab of case 8 (real stack).
import { chromium } from "@playwright/test";

const base = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3001";
const out = process.env.SHOT_DIR ?? "../docs/evidence";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 2600 } });
await page.goto(`${base}/cases/8`);
await page.getByRole("button", { name: "Story", exact: true }).click();
const checks = page.getByTestId("evidence-audit");
await checks.waitFor({ timeout: 30000 });
await page.waitForTimeout(1500);
await checks.screenshot({ path: `${out}/evidence-audit-checks.png` });
console.log(await page.getByTestId("audit-model").locator("p").first().innerText());
console.log(await page.getByTestId("audit-counts").innerText());
console.log("items shown:", await page.getByTestId("audit-item").count());
console.log("apply/correct buttons:", await page.getByRole("button", { name: /apply|correct/i }).count());
await browser.close();
