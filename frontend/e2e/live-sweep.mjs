// Live sweep: real backend + real frontend, no mocks.
// Usage: node e2e/live-sweep.mjs
import { chromium } from "@playwright/test";

const BASE = "http://localhost:3000";
const pages = [
  ["/", "Dashboard"],
  ["/discover", "Find New Cases"],
  ["/cases", "Cases"],
  ["/cases/1", "All cases"],
  ["/research", "Research"],
  ["/settings", "Settings"],
  ["/database", "Database"],
  ["/studio", "Story Studio"],
];

const browser = await chromium.launch();
let failures = 0;

for (const width of [1440, 1024, 768, 390]) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  const errors = [];
  const failed = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text()}`);
  });
  page.on("requestfailed", (r) => failed.push(`${r.failure()?.errorText} ${r.url()}`));
  page.on("response", (r) => {
    if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`);
  });

  console.log(`\n===== viewport ${width}px =====`);
  for (const [path, expected] of pages) {
    await page.goto(BASE + path, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(path === "/" ? 3000 : 2200);
    const text = await page.locator("body").innerText().catch(() => "");
    const hasExpected = text.includes(expected);
    const skeletons = await page.locator(".animate-pulse").count();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    const status =
      !hasExpected ? "MISSING-TEXT" : skeletons > 0 ? `${skeletons} SKELETONS` : "ok";
    const mark = status === "ok" ? "✓" : "✘";
    if (status !== "ok" || overflow > 2) failures++;
    console.log(
      `${mark} ${path.padEnd(12)} ${status.padEnd(15)} overflow-x=${overflow}px`,
    );
  }
  if (errors.length || failed.length) {
    failures++;
    console.log("  console errors:", [...new Set(errors)].slice(0, 10));
    console.log("  failed reqs:", [...new Set(failed)].slice(0, 10));
  }
  await page.close();
}

// Case workspace: exercise every tab against the live backend.
{
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  const failed = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400 && r.url().includes("/api/")) failed.push(`${r.status()} ${r.url()}`);
  });
  console.log("\n===== case workspace tabs (live) =====");
  await page.goto(`${BASE}/cases/1`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2000);
  for (const tab of ["Sources", "Facts", "Timeline", "Contradictions", "Story", "Research Languages", "Localizations", "Agent Runs", "Overview"]) {
    await page.getByRole("button", { name: tab, exact: true }).click();
    await page.waitForTimeout(1500);
    const skeletons = await page.locator(".animate-pulse").count();
    console.log(`${skeletons === 0 ? "✓" : "✘"} tab "${tab}" skeletons=${skeletons}`);
    if (skeletons > 0) failures++;
  }
  if (errors.length || failed.length) {
    failures++;
    console.log("  console errors:", [...new Set(errors)].slice(0, 10));
    console.log("  api failures:", [...new Set(failed)].slice(0, 10));
  }
  await page.close();
}

await browser.close();
console.log(`\n${failures === 0 ? "ALL CLEAN" : `${failures} problem(s) found`}`);
process.exit(failures ? 1 : 0);
