import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

test.describe("Main Visual Planner picture review", () => {
  test("shows the first open picture with its requirement, five decisions and reasons", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await expect(page.getByTestId("review-queue-page")).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/2 left to review/);
    await expect(page.getByText("Bryan Kohberger")).toBeVisible();
    await expect(page.getByTestId("review-queue-decisions")).toContainText("Identity / context: needs_review");
    await expect(page.getByTestId("review-queue-reason")).toContainText("identity from the page caption only");
  });

  test("Approve records the decision and the counter drops", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: "Approve", exact: false }).click();
    await expect(page.getByText(/Approved VIS_101/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/1 left to review/);
  });

  test("Reject records the decision and the counter drops", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: "Reject", exact: false }).click();
    await expect(page.getByText(/Rejected VIS_101/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/1 left to review/);
  });

  test("keyboard shortcuts drive Approve and Reject until the queue is empty", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByTestId("review-queue-page").click();
    await page.keyboard.press("a");
    await expect(page.getByText(/Approved VIS_101/)).toBeVisible();
    await page.keyboard.press("r");
    await expect(page.getByText(/Rejected VIS_205/)).toBeVisible();
    await expect(page.getByTestId("review-queue-empty")).toBeVisible();
  });
});
