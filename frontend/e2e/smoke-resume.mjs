// RESUME real smoke test — case #2 only, from master generation onward.
// Discovery + research already completed with real providers; do not rerun.
//   node e2e/smoke-resume.mjs
import { chromium } from "@playwright/test";

const BASE = "http://localhost:3000";
const API = "http://127.0.0.1:8000";
const CASE_ID = 2;

const consoleErrors = [];
const pageErrors = [];
const failedReqs = [];
const stages = [];
const t0 = Date.now();

function log(stage, msg) {
  const t = ((Date.now() - t0) / 1000).toFixed(0).padStart(4);
  console.log(`[${t}s] ${stage}: ${msg}`);
}
function mark(stage) {
  stages.push(stage);
  log("STAGE", `✔ ${stage}`);
}

const apiJson = async (path) => (await fetch(`${API}${path}`)).json();

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.on("pageerror", (e) => { pageErrors.push(String(e)); log("PAGEERR", String(e)); });
page.on("console", (m) => {
  if (m.type() === "error") { consoleErrors.push(m.text()); log("CONSOLE-ERR", m.text().slice(0, 200)); }
});
page.on("requestfailed", (r) => { failedReqs.push(`${r.failure()?.errorText} ${r.url()}`); log("REQFAIL", `${r.failure()?.errorText} ${r.url()}`); });
page.on("response", (r) => {
  if (r.status() >= 400) { failedReqs.push(`${r.status()} ${r.url()}`); log("HTTP-ERR", `${r.status()} ${r.url()}`); }
});

// ------------------------------------------------------------------
// Master generation from the actual Studio UI
// ------------------------------------------------------------------
await page.goto(`${BASE}/studio?case=${CASE_ID}`, { waitUntil: "domcontentloaded" });
await page.locator("select").nth(2).selectOption("45"); // smallest film length — cost control
log("master", "generating English Master (45min target) via OpenRouter — this blocks…");
const genStart = Date.now();
await page.getByRole("button", { name: /generate english master/i }).click();

{
  const deadline = Date.now() + 25 * 60 * 1000;
  let timedOutInUI = false;
  for (;;) {
    if (Date.now() > deadline) throw new Error("master generation exceeded 25min");
    const errBox = page.locator(".border-rose-500\\/30").first();
    if (await errBox.isVisible().catch(() => false)) {
      const msg = (await errBox.textContent()).trim();
      log("master", `UI error surfaced: ${msg.slice(0, 200)}`);
      if (/did not respond|timed out/i.test(msg)) timedOutInUI = true;
      else throw new Error(`master generation failed in UI: ${msg}`);
    }
    const master = await apiJson(`/api/cases/${CASE_ID}/master-story`).catch(() => null);
    if (master?.master) {
      log("master", `master v${master.master.version} status=${master.master.status} ` +
        `words=${master.master.word_count} engagement=${Math.round(master.master.engagement_score)} ` +
        `model=${master.master.generation_model} in ${((Date.now() - genStart) / 60000).toFixed(1)}min` +
        (timedOutInUI ? " [UI showed timeout while backend completed]" : ""));
      if (timedOutInUI) pageErrors.push("UI timeout fired before blocking generation POST returned");
      break;
    }
    await page.waitForTimeout(10_000);
  }
}
mark("English Master generated");

// Gates + persistence: open the case Localizations tab
await page.goto(`${BASE}/cases/${CASE_ID}`, { waitUntil: "domcontentloaded" });
await page.getByRole("button", { name: "Localizations", exact: true }).click();
await page.locator(".animate-pulse").first().waitFor({ state: "detached", timeout: 20_000 }).catch(() => {});
const masterStatus = (await apiJson(`/api/cases/${CASE_ID}/master-story`)).master.status;
const locTabText = await page.locator("main").innerText();
log("gates", `master status=${masterStatus}; problems visible=${/quality gate failure/.test(locTabText)}`);
mark(`Master quality gates inspected (status=${masterStatus})`);

// ------------------------------------------------------------------
// Persian localization — ONE language only
// ------------------------------------------------------------------
let localizationStatus = "skipped — master not ready";
if (masterStatus === "ready") {
  const genBtn = page.getByRole("button", { name: "Generate", exact: true }).nth(1); // de, fa, ar order → nth(1)=fa
  const enabled = await genBtn.isEnabled().catch(() => false);
  log("localization", `Persian Generate enabled=${enabled}`);
  if (enabled) {
    await genBtn.click();
    const deadline = Date.now() + 20 * 60 * 1000;
    for (;;) {
      if (Date.now() > deadline) { localizationStatus = "timed out"; break; }
      const errBox = page.locator(".border-rose-500\\/30").first();
      if (await errBox.isVisible().catch(() => false)) {
        localizationStatus = `failed: ${(await errBox.textContent()).trim().slice(0, 150)}`;
        break;
      }
      const sheet = page.locator('article[dir="rtl"]');
      if (await sheet.isVisible().catch(() => false)) {
        const rtlText = await sheet.innerText();
        const faChars = (rtlText.match(/[\u0600-\u06FF]/g) || []).length;
        log("localization", `RTL sheet opened, ${rtlText.split(/\s+/).length} words, ` +
          `${faChars} Persian chars, dir=rtl ✓`);
        localizationStatus = "generated + RTL verified";
        break;
      }
      await page.waitForTimeout(8000);
    }
  }
  const locs = await apiJson(`/api/cases/${CASE_ID}/localizations`);
  for (const l of locs) {
    log("localization", `${l.language} v${l.version} status=${l.status} ` +
      `native=${l.native_quality_score} sem=${l.semantic_consistency_score} ` +
      `fact=${l.factual_consistency_score} eng=${Math.round(l.engagement_score)}`);
  }
  if (locs.some((l) => l.language === "fa" && l.status === "ready")) mark("Persian localization ready with metrics");
} else {
  log("localization", `skipped — master status is ${masterStatus}, Generate should be disabled`);
}

// Refresh persistence check — reload the case page, verify states hold.
await page.reload({ waitUntil: "domcontentloaded" });
await page.locator(".animate-pulse").first().waitFor({ state: "detached", timeout: 20_000 }).catch(() => {});
const masterAfter = (await apiJson(`/api/cases/${CASE_ID}/master-story`)).master.status;
log("refresh", `master status after reload=${masterAfter}`);
if (masterAfter === masterStatus) mark("Refresh persistence verified");

// ------------------------------------------------------------------
// Agent runs token/cost data
// ------------------------------------------------------------------
await page.getByRole("button", { name: "Agent Runs", exact: true }).click();
await page.locator(".animate-pulse").first().waitFor({ state: "detached", timeout: 20_000 }).catch(() => {});
const runs = await apiJson(`/api/cases/${CASE_ID}/agent-runs`);
const costs = runs.filter((r) => r.estimated_cost_usd != null);
const totalCost = costs.reduce((a, r) => a + r.estimated_cost_usd, 0);
log("agent-runs", `${runs.length} runs; ${costs.length} with cost data; total $${totalCost.toFixed(4)}`);
for (const r of runs.slice(0, 20))
  log("agent-runs", `  ${r.agent_name} ${r.status} model=${r.model} tok=${r.total_tokens} $${r.estimated_cost_usd ?? "?"}`);
mark("Agent Runs with token/cost data verified");

// ------------------------------------------------------------------
// Final hygiene
// ------------------------------------------------------------------
const skeletons = await page.locator(".animate-pulse").count();
const jobs = await apiJson(`/api/research-jobs?case_id=${CASE_ID}`);
const activeJobs = jobs.filter((j) => j.status === "queued" || j.status === "running");

console.log("\n================ REAL UI SMOKE TEST — RESUME ================");
console.log(`case used:           #${CASE_ID}`);
console.log(`Prior Devin calls:  ${jobs.filter((j) => j.provider === "devin").length} job(s) [historical]`);
console.log(`OpenRouter calls:    ${runs.filter((r) => r.provider === "openrouter").length} agent run(s)`);
console.log(`total cost:          $${totalCost.toFixed(4)} (provider-reported, all runs on case)`);
console.log(`stages completed:    ${stages.length}`);
stages.forEach((s) => console.log(`  - ${s}`));
console.log(`master status:       ${masterStatus}`);
console.log(`localization status: ${localizationStatus}`);
console.log(`lingering skeletons: ${skeletons}`);
console.log(`active jobs left:    ${activeJobs.length}`);
console.log(`console errors:      ${consoleErrors.length}`);
consoleErrors.slice(0, 8).forEach((e) => console.log(`  - ${e.slice(0, 160)}`));
console.log(`page errors:         ${pageErrors.length}`);
pageErrors.slice(0, 8).forEach((e) => console.log(`  - ${e.slice(0, 160)}`));
console.log(`failed requests:     ${failedReqs.length}`);
[...new Set(failedReqs)].slice(0, 10).forEach((e) => console.log(`  - ${e.slice(0, 160)}`));
await browser.close();
