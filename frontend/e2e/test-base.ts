import { expect, test as base } from "@playwright/test";

/**
 * Next.js sends server-rendered HTML first and attaches the click handlers
 * ("hydration") a moment later. A test that clicks straight after `goto` can hit
 * a button that looks ready but does nothing yet — an intermittent failure.
 * This wraps `goto` so every navigation waits until React has taken over the page.
 */
export const test = base.extend({
  page: async ({ page }, provide) => {
    const goto = page.goto.bind(page);
    page.goto = (async (...args: Parameters<typeof goto>) => {
      const response = await goto(...args);
      await page.waitForFunction(() => {
        const root = document.querySelector("main") ?? document.body;
        return Array.from(root.querySelectorAll("*")).some((el) =>
          Object.keys(el).some((k) => k.startsWith("__reactFiber$")),
        );
      });
      return response;
    }) as typeof page.goto;
    await provide(page);
  },
});

export { expect };
