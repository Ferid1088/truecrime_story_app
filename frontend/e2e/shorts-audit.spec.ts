import { expect, test } from "./test-base";
import { mockApi } from "./fixtures";

/**
 * This is intentionally a click-through inventory, not a product assertion.
 * It records whether each control changes the visible UI and which API calls
 * were made while doing so. The audit document contains the resulting table.
 */
test("shorts controls click-through inventory", async ({ page }) => {
  await mockApi(page);
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/")) requests.push(`${request.method()} ${new URL(request.url()).pathname}`);
  });
  const results: Array<{ control: string; result: string; api: string }> = [];
  await page.goto("/short-form");
  await expect(page.getByTestId("short-form-workspace")).toBeVisible();
  await expect(page.getByText("Not implemented yet — candidate approve, edit, regenerate, and reject actions have no persisted backend.")).toBeVisible();
  for (const control of ["Regenerate batch", "Edit C6-S01", "Approve", "Edit", "Regenerate", "Reject"]) {
    await expect(page.getByRole("button", { name: control, exact: true })).toHaveCount(0);
    results.push({ control: `Candidate / ${control}`, result: "removed (not implemented yet)", api: "none" });
  }

  for (const tab of ["Approved", "YouTube Shorts", "Instagram Reels", "Facebook Reels", "TikTok", "Publishing Plan"]) {
    await page.getByRole("tab", { name: tab, exact: true }).click();
    await expect(page.getByRole("tabpanel", { name: tab })).toBeVisible();
    results.push({ control: `Tab / ${tab}`, result: "works (local UI)", api: "none" });
  }
  await page.getByRole("tab", { name: "Settings", exact: true }).click();
  await page.getByRole("checkbox").first().click();
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Saved episode override", { exact: true })).toBeVisible();
  results.push({ control: "Settings / Save", result: "works", api: requests.filter((x) => x === "PATCH /api/short-form/settings").join(", ") });
  await page.getByRole("tab", { name: "Performance", exact: true }).click();
  await expect(page.getByText(/performance data|metric rows/i).first()).toBeVisible();
  results.push({ control: "Performance / load", result: "works", api: requests.filter((x) => x === "GET /api/short-form/metrics").join(", ") });

  await page.goto("/short-form/review");
  await expect(page.getByText("Not implemented yet — this pilot review has no persisted backend.")).toBeVisible();
  for (const field of ["Caption", "Destination", "CTA"]) {
    await expect(page.getByLabel(field)).toBeDisabled();
    results.push({ control: `Review / ${field}`, result: "disabled (not implemented yet)", api: "none" });
  }
  for (const control of ["Approve", "Reject"]) {
    await expect(page.getByRole("button", { name: control, exact: true })).toBeDisabled();
    results.push({ control: `Review / ${control}`, result: "disabled (not implemented yet)", api: "none" });
  }

  console.log(`SHORTS_AUDIT ${JSON.stringify(results)}`);
});
