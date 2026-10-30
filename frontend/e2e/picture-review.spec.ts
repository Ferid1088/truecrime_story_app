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

  test("a picture can be checked as must-be-used and given a drawing priority, a clip only the checkbox", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await page.getByTestId("review-queue-must-use").click();
    await expect(page.getByTestId("review-queue-selection")).toContainText("Saved.");
    await expect(page.getByTestId("review-queue-must-use")).toBeChecked();
    await page.getByTestId("review-queue-draw-priority").selectOption("3");
    await expect(page.getByTestId("review-queue-draw-priority")).toHaveValue("3");
    await expect(page.getByRole("button", { name: "Trimmed & cut" })).toBeVisible();
    await page.getByRole("button", { name: /^Next \(N\)/ }).click();
    await page.getByRole("button", { name: "Previous", exact: false }).click();
    await expect(page.getByTestId("review-queue-draw-priority")).toHaveValue("3");
    await expect(page.getByTestId("review-queue-must-use")).toBeChecked();
    await expect(page.getByTestId("review-queue-chosen").first()).toContainText("3");
    await expect(page.getByTestId("review-queue-chosen-viewer")).toContainText("Drawing priority 3");
    await page.getByTestId("review-queue-draw-priority").selectOption("0");
    await expect(page.getByTestId("review-queue-chosen")).toHaveCount(0);
    await expect(page.getByTestId("review-queue-draw-priority")).toHaveValue("0");
  });

  test("opened from the portrait stop it explains what to do and shows the approved photos", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review?portraits=1");
    await expect(page.getByTestId("review-queue-portraits-note")).toContainText("Drawing priority 1");
    await expect(page.getByTestId("review-queue-shown").getByRole("button", { name: "Approved" })).toBeVisible();
  });

  test("the planner says how many portraits the film needs and the button makes them", async ({ page }) => {
    await mockApi(page);
    await page.goto("/documentary/1/review");
    await expect(page.getByTestId("portraits-summary")).toContainText("The film needs 2 portraits");
    await expect(page.getByTestId("portrait-status-VIS_103")).toHaveText("reserve");
    await page.getByTestId("create-portraits").click();
    await expect(page.getByTestId("portrait-status-VIS_101")).toContainText("being drawn 20 %");
    await expect(page.getByTestId("create-portraits")).toBeDisabled();
    await expect(page.getByTestId("portraits-check")).toContainText("The planner ordered VIS_103 and checks again");
  });
});
