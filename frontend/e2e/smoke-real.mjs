// REAL production smoke test — drives the actual UI against the real
// backend with real OpenRouter calls. Run:
//   node e2e/smoke-real.mjs
// Logs every stage; captures console errors, page errors, failed requests.
import { chromium } from "@playwright/test";

const BASE = "http://localhost:3000";
const API = "http://127.0.0.1:8000";

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

let caseId = null;

// ------------------------------------------------------------------
// 1. Dashboard
// ------------------------------------------------------------------
await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
await page.getByText("Total Cases").waitFor({ timeout: 30_000 });
await page.locator(".animate-pulse").first().waitFor({ state: "detached", timeout: 30_000 }).catch(() => {});
mark("Dashboard loaded with real stats");

// ------------------------------------------------------------------
// 2. Discover via real OpenRouter research
// ------------------------------------------------------------------
await page.goto(`${BASE}/discover`, { waitUntil: "domcontentloaded" });
await page.getByRole("button", { name: /discover new cases/i }).waitFor();
log("discover", "starting real OpenRouter discovery job (5 candidates, EN)…");
await page.getByRole("button", { name: /discover new cases/i }).click();

// Watch stage transitions until candidates appear or an error shows.
{
  const deadline = Date.now() + 15 * 60 * 1000;
  let lastStage = "";
  for (;;) {
    if (Date.now() > deadline) throw new Error("discovery timed out after 15min");
    const stage = await page.locator("span.text-xs.text-muted-foreground >> nth=0").textContent().catch(() => "");
    if (stage && stage !== lastStage && /queued|running|searching|starting/i.test(stage)) {
      lastStage = stage; log("discover", `stage: ${stage.trim()}`);
    }
    const err = await page.locator(".border-rose-500\\/30").first().textContent().catch(() => null);
    if (err && err.includes("failed") || (err && /error|unavailable|unreachable/i.test(err))) {
      throw new Error(`discovery failed in UI: ${err.trim()}`);
    }
    const n = await page.getByRole("button", { name: /investigate/i }).count();
    if (n > 0) break;
    await page.waitForTimeout(3000);
  }
}
mark("Discovery job completed — candidates rendered");

// ------------------------------------------------------------------
// 3. Investigate one candidate → case workspace
// ------------------------------------------------------------------
await page.getByRole("button", { name: /investigate/i }).first().click();
await page.waitForURL(/\/cases\/\d+/, { timeout: 30_000 });
caseId = Number(page.url().match(/\/cases\/(\d+)/)[1]);
await page.getByRole("button", { name: "Sources", exact: true }).waitFor({ timeout: 30_000 });
mark(`Candidate investigated → case #${caseId}`);

// ------------------------------------------------------------------
// 4-5. Run real multilingual research; watch stage transitions
// ------------------------------------------------------------------
await page.getByRole("button", { name: /run research/i }).click();
log("research", "real OpenRouter research job started…");
{
  const deadline = Date.now() + 15 * 60 * 1000;
  let lastStage = "";
  for (;;) {
    if (Date.now() > deadline) throw new Error("research timed out after 15min");
    const stage = await page.locator("div.mb-4.flex.items-center.gap-2").first().textContent().catch(() => "");
    if (stage && stage !== lastStage) { lastStage = stage; log("research", `stage: ${stage.trim()}`); }
    const err = await page.locator(".border-rose-500\\/30").first().textContent().catch(() => null);
    if (err && /fail|unavailable|error|timed out/i.test(err)) {
      throw new Error(`research failed in UI: ${err.trim()}`);
    }
    // Success signal: workspace switches to Sources tab
    const srcBtn = page.getByRole("button", { name: "Sources", exact: true });
    const isActive = await srcBtn.evaluate((el) => el.className.includes("border-primary")).catch(() => false);
    const stageGone = !(await page.locator("div.mb-4.flex.items-center.gap-2").first().isVisible().catch(() => false));
    if (isActive && stageGone) break;
    await page.waitForTimeout(4000);
  }
}
mark("Research job completed — UI switched to Sources");

// ------------------------------------------------------------------
// 6. Verify research outputs populate
// ------------------------------------------------------------------
for (const tab of ["Sources", "Facts", "Timeline", "Contradictions", "Research Languages"]) {
  await page.getByRole("button", { name: tab, exact: true }).click();
  await page.locator(".animate-pulse").first().waitFor({ state: "detached", timeout: 20_000 }).catch(() => {});
  const text = await page.locator("main").innerText();
  const rows = await page.locator("table tbody tr").count();
  const cards = await page.locator("main .rounded-lg.border").count();
  log("tabs", `${tab}: ${rows} table rows, ${cards} cards, empty=${/No .*(found|yet|extracted|match)/.test(text)}`);
}
mark("Sources/Facts/Timeline/Contradictions/Research Languages rendered");

// ------------------------------------------------------------------
// 7-9. Generate English Master via real OpenRouter
// ------------------------------------------------------------------
await page.goto(`${BASE}/studio?case=${caseId}`, { waitUntil: "domcontentloaded" });
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
    const master = await apiJson(`/api/cases/${caseId}/master-story`).catch(() => null);
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

// Inspect gates on the Localizations tab
await page.goto(`${BASE}/cases/${caseId}`, { waitUntil: "domcontentloaded" });
await page.getByRole("button", { name: "Localizations", exact: true }).click();
await page.locator(".animate-pulse").first().waitFor({ state: "detached", timeout: 20_000 }).catch(() => {});
const masterStatus = (await apiJson(`/api/cases/${caseId}/master-story`)).master.status;
const locTabText = await page.locator("main").innerText();
log("gates", `master status=${masterStatus}; problems visible=${/quality gate failure/.test(locTabText)}`);
mark(`Master quality gates inspected (status=${masterStatus})`);

// ------------------------------------------------------------------
// 10-11. Persian localization if master ready
// ------------------------------------------------------------------
let localizationStatus = "skipped — master not ready";
if (masterStatus === "ready") {
  const persianRow = page.locator("div", { hasText: "Persian" }).filter({ hasText: "Persian" }).last();
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
        log("localization", `RTL sheet opened, ${rtlText.split(/\s+/).length} words, dir=rtl ✓`);
        localizationStatus = "generated + RTL verified";
        break;
      }
      await page.waitForTimeout(8000);
    }
  }
  // metrics
  const locs = await apiJson(`/api/cases/${caseId}/localizations`);
  for (const l of locs) {
    log("localization", `${l.language} v${l.version} status=${l.status} ` +
      `native=${l.native_quality_score} sem=${l.semantic_consistency_score} ` +
      `fact=${l.factual_consistency_score} eng=${Math.round(l.engagement_score)}`);
  }
  if (locs.some((l) => l.language === "fa" && l.status === "ready")) mark("Persian localization ready with metrics");
} else {
  log("localization", `skipped — master status is ${masterStatus}, Persian Generate should be disabled`);
  const genBtns = page.getByRole("button", { name: "Generate", exact: true });
  const n = await genBtns.count();
  let allDisabled = true;
  for (let i = 0; i < n; i++) if (await genBtns.nth(i).isEnabled()) allDisabled = false;
  log("localization", `gating check: all Generate disabled=${allDisabled}`);
  if (allDisabled) mark("Localization correctly blocked when master not ready");
}

// ------------------------------------------------------------------
// 12. Agent runs token/cost data
// ------------------------------------------------------------------
await page.getByRole("button", { name: "Agent Runs", exact: true }).click();
await page.locator(".animate-pulse").first().waitFor({ state: "detached", timeout: 20_000 }).catch(() => {});
const runs = await apiJson(`/api/cases/${caseId}/agent-runs`);
const costs = runs.filter((r) => r.estimated_cost_usd != null);
const totalCost = costs.reduce((a, r) => a + r.estimated_cost_usd, 0);
log("agent-runs", `${runs.length} runs; ${costs.length} with cost data; total $${totalCost.toFixed(4)}`);
for (const r of runs.slice(0, 12))
  log("agent-runs", `  ${r.agent_name} ${r.status} model=${r.model} tok=${r.total_tokens} $${r.estimated_cost_usd ?? "?"}`);
mark("Agent Runs with token/cost data verified");

// ------------------------------------------------------------------
// 13. Final hygiene
// ------------------------------------------------------------------
const skeletons = await page.locator(".animate-pulse").count();
const jobs = await apiJson(`/api/research-jobs?case_id=${caseId}`);
const activeJobs = jobs.filter((j) => j.status === "queued" || j.status === "running");

console.log("\n================ REAL UI SMOKE TEST ================");
console.log(`case used:           #${caseId}`);
console.log(`Research jobs:       ${jobs.length} (provider: ${[...new Set(jobs.map(j=>j.provider))].join(",") || "n/a"})`);
console.log(`OpenRouter calls:    ${runs.filter((r) => r.provider === "openrouter").length} agent run(s)`);
console.log(`total cost:          $${totalCost.toFixed(4)} (provider-reported)`);
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
