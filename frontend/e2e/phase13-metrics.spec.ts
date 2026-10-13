import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

test("Shorts test report shows persisted production status", async ({ page }) => {
  await mockApi(page);
  await page.goto("/short-form");
  await page.getByRole("tab", { name: "Test Report", exact: true }).click();
  await expect(page.getByRole("tabpanel", { name: "Test Report" }).getByText("Candidates")).toBeVisible();
  await expect(page.getByRole("tabpanel", { name: "Test Report" }).getByText("Rendered")).toBeVisible();
});
