import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

test.describe("P10 picture review queue", () => {
  test("shows the first picture with its beat, entity and verifier reason, sorted by beat count", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await expect(page.getByTestId("review-queue-page")).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/2 left to review/);
    await expect(page.getByText("Bryan Kohberger")).toBeVisible();
    await expect(page.getByTestId("review-queue-beats")).toContainText("The suspect was arrested in Pennsylvania.");
    await expect(page.getByTestId("review-queue-reason")).toContainText("identity from the page caption only");
    await expect(page.locator("img[alt='Suspect photo']")).toBeVisible();
  });

  test("Use approves the picture and the counter drops", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: "Use", exact: false }).click();
    await expect(page.getByText(/Used VIS_101/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/1 left to review/);
  });

  test("Reject rejects the picture and the counter drops", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: "Reject", exact: false }).click();
    await expect(page.getByText(/Rejected VIS_101/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/1 left to review/);
  });

  test("Wrong person records the negative example and the counter drops", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: "Wrong person", exact: false }).click();
    await expect(page.getByText(/added to its "not this" list/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/1 left to review/);
  });

  test("keyboard shortcuts drive Use, Reject, and Next", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByTestId("review-queue-page").click(); // focus the page, away from any input
    await page.keyboard.press("a");
    await expect(page.getByText(/Used VIS_101/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/1 left to review/);
    await page.keyboard.press("r");
    await expect(page.getByText(/Rejected VIS_205/)).toBeVisible();
    await expect(page.getByTestId("review-queue-empty")).toBeVisible();
  });

  test("an empty queue shows the empty state, not a crash", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: "Use", exact: false }).click();
    await page.getByRole("button", { name: "Use", exact: false }).click();
    await expect(page.getByTestId("review-queue-empty")).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/0 left to review/);
  });
});
