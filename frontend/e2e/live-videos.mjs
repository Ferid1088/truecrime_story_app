import { chromium } from "@playwright/test";
const base = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3001";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 } });
await page.goto(`${base}/cases/8`);
await page.getByRole("button", { name: "Videos", exact: true }).click();
await page.getByText("research only").first().waitFor({ timeout: 30000 });
await page.screenshot({ path: "../docs/evidence/videos-use-and-rights.png" });
await browser.close();
