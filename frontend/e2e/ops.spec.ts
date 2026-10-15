import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

test.describe("Operations page", () => {
  test("shows totals, services and failed calls", async ({ page }) => {
    await mockApi(page);
    await page.goto("/ops");
    await expect(page.getByRole("heading", { name: "Operations" })).toBeVisible();
    await expect(page.getByText("api.llm.example")).toBeVisible();
    await expect(page.getByText("$1.23")).toBeVisible();
    await expect(page.getByText("1 / 2")).toBeVisible(); // running / queued
    await expect(page.getByText("abc123def456")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Audit log" })).toBeVisible();
    await expect(page.getByText("No human overrides recorded")).toBeVisible();
    await expect(page.locator(".animate-pulse")).toHaveCount(0);
  });

  test("switching the window asks the API for that window", async ({ page }) => {
    await mockApi(page);
    await page.goto("/ops");
    const req = page.waitForRequest((r) => r.url().includes("/api/ops/summary?hours=168"));
    await page.getByRole("button", { name: "7 days" }).click();
    await req;
  });

  test("an API failure shows an error with retry", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/ops/summary**", (r) =>
      r.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"ops down"}' }),
    );
    await page.goto("/ops");
    await expect(page.getByText("ops down")).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  test("sign-out appears only when the server requires a login", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Sign out" })).toHaveCount(0);
    await page.route("**/api/auth/session", (r) =>
      r.fulfill({ contentType: "application/json", body: '{"authenticated":true,"auth_required":true}' }),
    );
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Sign out" }).first()).toBeVisible();
  });
});
