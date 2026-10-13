import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

test("Mock run is labelled honestly and manual Shorts enqueue reaches done", async ({ page }) => {
  await mockApi(page);
  await page.goto("/short-form");
  await page.getByRole("tab", { name: "Mock run", exact: true }).click();
  await expect(page.getByRole("tabpanel", { name: "Mock run" }).getByText("Candidates")).toBeVisible();
  await expect(page.getByRole("tabpanel", { name: "Mock run" }).getByText("Real renders")).toBeVisible();
  await page.getByRole("button", { name: "Create shorts" }).first().click();
  await expect(page.getByTestId("shorts-status-en")).toHaveText("queued");
  await expect(page.getByTestId("shorts-status-en")).toHaveText("running", { timeout: 5_000 });
  await expect(page.getByTestId("shorts-status-en")).toHaveText("done", { timeout: 5_000 });
  await expect.poll(() => page.evaluate(async () => (await fetch("/api/short-form/files/201/video")).ok)).toBeTruthy();
});
