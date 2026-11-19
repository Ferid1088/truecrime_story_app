import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

test.describe("Case-level picture approval", () => {
  test("shows the gate closed, approves once, and can withdraw the approval", async ({ page }) => {
    await mockApi(page);
    let state: "awaiting_owner" | "approved" = "awaiting_owner";
    const body = () => state === "approved"
      ? { state, open: true, approved_at: "2026-11-18T20:00:00Z", note: null }
      : { state, open: false, reason: "The owner has not approved the case's pictures and videos." };
    await page.route("**/api/cases/1/mvp/visual-approval", (route) => {
      const method = route.request().method();
      if (method === "POST") state = "approved";
      if (method === "DELETE") state = "awaiting_owner";
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body()) });
    });
    await page.goto("/documentary/1/review");
    const bar = page.getByTestId("visual-approval");
    await expect(bar).toContainText("No film is directed or rendered before you approve");
    await expect(page.getByTestId("visual-approval-state")).toHaveText("Not approved");
    await bar.getByRole("button", { name: "Approve pictures and videos" }).click();
    await expect(page.getByTestId("visual-approval-state")).toHaveText("Approved");
    await expect(bar).toContainText("a change to the pictures asks for a new approval");
    await bar.getByRole("button", { name: "Withdraw approval" }).click();
    await expect(page.getByTestId("visual-approval-state")).toHaveText("Not approved");
  });

  test("a case with no picture plan cannot be approved", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/cases/1/mvp/visual-approval", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ state: "none", open: false, reason: "The English picture plan is not made yet." }) }));
    await page.goto("/documentary/1/review");
    await expect(page.getByTestId("visual-approval-state")).toHaveText("No picture plan yet");
    await expect(page.getByRole("button", { name: "Approve pictures and videos" })).toBeDisabled();
  });
});
