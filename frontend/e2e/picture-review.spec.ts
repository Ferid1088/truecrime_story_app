import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

test.describe("Main Visual Planner picture review", () => {
  test("shows the first open picture with its requirement, five decisions and reasons", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await expect(page.getByTestId("review-queue-page")).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/1 \/ 2 · 2 left to review/);
    await expect(page.getByText("Bryan Kohberger").last()).toBeVisible();
    await expect(page.getByTestId("review-queue-list")).toContainText("Bryan Kohberger");
    await expect(page.getByTestId("review-queue-requirements")).toContainText("also offered for 1 more requirement");
    await expect(page.getByTestId("review-queue-decisions")).toContainText("Identity / context: needs_review");
    await expect(page.getByTestId("review-queue-reason")).toContainText("identity from the page caption only");
  });

  test("Approve records the decision, moves to the next picture and the position stays in the list", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: /^Approve \(A\)/ }).click();
    await expect(page.getByText(/Approved VIS_101 for 2 requirements/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/2 \/ 2 · 1 left to review.*1 decided/);
    await expect(page.getByText("Ka-Bar sheath").last()).toBeVisible();
  });

  test("Reject records the decision and a decided picture can be decided again", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByRole("button", { name: /^Reject \(R\)/ }).click();
    await expect(page.getByText(/Rejected VIS_101/)).toBeVisible();
    await page.getByRole("button", { name: "Previous", exact: false }).click();
    await expect(page.getByTestId("review-queue-verdict")).toContainText("Rejected");
    await page.getByRole("button", { name: /^Approve \(A\)/ }).click();
    await expect(page.getByText(/Approved VIS_101/)).toBeVisible();
  });

  test("keyboard shortcuts drive Approve and Reject until the queue is empty", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByTestId("review-queue-page").click();
    await page.keyboard.press("a");
    await expect(page.getByText(/Approved VIS_101/)).toBeVisible();
    await page.keyboard.press("r");
    await expect(page.getByText(/Rejected VIS_205/)).toBeVisible();
    await expect(page.getByTestId("review-queue-counter")).toHaveText(/0 left to review.*2 decided/);
  });
});
