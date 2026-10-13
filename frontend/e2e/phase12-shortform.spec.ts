import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const TABS = ["Candidates", "Rendered shorts", "Settings", "Test Report"];

test.describe("Phase 12 short-form workspace", () => {
  test("renders persisted candidates and production defaults", async ({ page }) => {
    await mockApi(page);
    await page.goto("/short-form");
    await expect(page.getByTestId("short-form-workspace")).toBeVisible();
    for (const tab of TABS) await expect(page.getByRole("tab", { name: tab, exact: true })).toBeVisible();
    await expect(page.getByText("1 persisted candidates · 1 language renders")).toBeVisible();
    await expect(page.getByTestId("candidate-101")).toBeVisible();
    await expect(page.getByText("Shorts stages")).toBeVisible();
  });

  test("candidate actions use preview then apply", async ({ page }) => {
    await mockApi(page);
    await page.goto("/short-form");
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(page.getByTestId("short-form-preview")).toBeVisible();
    await page.getByRole("button", { name: "Apply", exact: true }).click();
    await expect(page.getByText("Applied to the selected short only.")).toBeVisible();
  });

  test("rendered short is playable and shows cover/caption metadata", async ({ page }) => {
    await mockApi(page);
    await page.goto("/short-form");
    await page.getByRole("tab", { name: "Rendered shorts", exact: true }).click();
    await expect(page.locator("video")).toBeVisible();
    await expect(page.getByText("1080 × 1920")).toBeVisible();
    await expect(page.getByText("The log fixes the last known time.")).toBeVisible();
  });
});
