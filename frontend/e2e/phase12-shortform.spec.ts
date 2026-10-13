import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const TABS = [
  "Candidates", "Approved", "YouTube Shorts", "Instagram Reels",
  "Facebook Reels", "TikTok", "Settings", "Publishing Plan",
  "Performance", "Test Report",
];

test.describe("Phase 12 short-form workspace", () => {
  test("renders the complete tab surface and real-data boundary", async ({ page }) => {
    await mockApi(page);
    await page.goto("/short-form");
    await expect(page.getByTestId("short-form-workspace")).toBeVisible();
    for (const tab of TABS) await expect(page.getByRole("tab", { name: tab, exact: true })).toBeVisible();
    await expect(page.getByText("Verification data only")).toBeVisible();
    await expect(page.getByText("5 unpersisted candidates")).toBeVisible();
    await expect(page.getByTestId("candidate-C6-S01")).toBeVisible();
  });

  test("pilot-only candidate actions are visibly unavailable", async ({ page }) => {
    await mockApi(page);
    await page.goto("/short-form");
    await expect(page.getByText("Not implemented yet — candidate approve, edit, regenerate, and reject actions have no persisted backend.")).toBeVisible();
    await page.getByRole("tab", { name: "Approved", exact: true }).click();
    await expect(page.getByText("Local gate passes are not persisted approvals.")).toBeVisible();
    await page.getByRole("tab", { name: "Publishing Plan", exact: true }).click();
    await expect(page.getByText("Day 14")).toBeVisible();
    await expect(page.getByText("Round-robin concept IDs are dry-run placeholders.")).toBeVisible();
  });

  test("platform preview exposes safe areas and audio gap", async ({ page }) => {
    await mockApi(page);
    await page.goto("/short-form");
    await page.getByRole("tab", { name: "TikTok", exact: true }).click();
    await expect(page.getByText("Safe areas")).toBeVisible();
    await expect(page.getByText("1080 x 1920")).toBeVisible();
    await expect(page.getByText("Original audio").last()).toBeVisible();
    await expect(page.getByText("Unavailable", { exact: true })).toBeVisible();
  });
});
