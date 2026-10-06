import { chromium } from "@playwright/test";
const browser = await chromium.launch();
for (const width of [768, 390]) {
  const page = await browser.newPage({ viewport: { width, height: 900 } });
  for (const path of ["/settings", "/database", "/cases/1"]) {
    await page.goto(`http://localhost:3000${path}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2500);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    console.log(`${width}px ${path}: overflow-x=${overflow}px`);
    if (overflow > 2) {
      const wide = await page.evaluate(() => {
        const out = [];
        for (const el of document.querySelectorAll("*")) {
          const r = el.getBoundingClientRect();
          if (r.width > document.documentElement.clientWidth + 2 && out.length < 8) {
            const cn = typeof el.className === "string" ? el.className.slice(0, 80) : "";
            out.push(`${el.tagName.toLowerCase()} w=${Math.round(r.width)} .${cn}`);
          }
        }
        return out;
      });
      console.log("   widest:", wide);
    }
  }
  await page.close();
}
await browser.close();
