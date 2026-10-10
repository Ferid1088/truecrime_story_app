import { test, expect } from "@playwright/test";
import { mockApi } from "./fixtures";

test("Performance compares concept types and shows honest attribution", async ({ page }) => {
  await mockApi(page);
  await page.goto("/short-form");
  await page.getByRole("tab", { name: "Performance", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Performance by concept type" })).toBeVisible();
  await expect(page.getByText("verdict-countdown")).toBeVisible();
  await expect(page.getByText("Attribution: unavailable")).toBeVisible();
  await expect(page.getByText("Conversion is not shown because real attribution cannot be computed for these mocked metrics.")).toBeVisible();
  await expect(page.locator("tbody")).not.toContainText(/conversion\s*rate\s*\d|conversion.*\d+%/i);
});
