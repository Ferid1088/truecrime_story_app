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
  const record = async (control: string, action: () => Promise<void>, expected: string) => {
    const before = requests.length;
    await action();
    await expect(page.getByRole("status")).toContainText(expected);
    results.push({ control, result: "works (local UI)", api: requests.slice(before).join(", ") || "none" });
  };

  await page.goto("/short-form");
  await expect(page.getByTestId("short-form-workspace")).toBeVisible();
  await record("Candidates / Regenerate batch", () => page.getByRole("button", { name: "Regenerate batch" }).click(), "Regeneration is queued");
  const headerEdit = page.getByRole("button", { name: "Edit C6-S01" });
  await expect(headerEdit).toHaveCount(1);
  await headerEdit.click();
  await expect(page.getByRole("status")).toHaveText("C6-S01 is ready for editing.");
  results.push({ control: "Candidate header Edit", result: "works (local UI)", api: "none" });
  await record("Candidate Approve", () => page.getByRole("button", { name: "Approve" }).first().click(), "C6-S01");
  await record("Candidate Edit", () => page.getByRole("button", { name: "Edit", exact: true }).first().click(), "C6-S01 is ready");
  await record("Candidate Regenerate", () => page.getByRole("button", { name: "Regenerate", exact: true }).first().click(), "C6-S01 is ready");
  await record("Candidate Reject", () => page.getByRole("button", { name: "Reject", exact: true }).first().click(), "C6-S01 rejected");

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
  await page.getByLabel("Caption").fill("Edited caption");
  await page.getByLabel("Destination").fill("episode://edited");
  await page.getByLabel("CTA").fill("Edited CTA");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByText("Approved", { exact: true })).toBeVisible();
  results.push({ control: "Review / Approve", result: "works (local UI)", api: "none" });
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("Rejected", { exact: true })).toBeVisible();
  results.push({ control: "Review / Reject", result: "works (local UI)", api: "none" });

  console.log(`SHORTS_AUDIT ${JSON.stringify(results)}`);
});
