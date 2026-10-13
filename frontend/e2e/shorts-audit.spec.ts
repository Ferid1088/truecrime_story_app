import { expect, test } from "./test-base";
import { mockApi } from "./fixtures";

test("shorts controls preview and apply a persisted candidate", async ({ page }) => {
  await mockApi(page);
  const requests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/short-form/")) {
      requests.push(`${request.method()} ${new URL(request.url()).pathname}`);
    }
  });
  await page.goto("/short-form");
  await expect(page.getByTestId("short-form-workspace")).toBeVisible();
  await page.getByRole("button", { name: "Approve", exact: true }).click();
  await expect(page.getByTestId("short-form-preview")).toBeVisible();
  await page.getByRole("button", { name: "Apply", exact: true }).click();
  await expect(page.getByText("Applied to the selected short only.")).toBeVisible();
  expect(requests.some((path) => path.startsWith("POST /api/short-form/1/candidates/101/preview"))).toBeTruthy();
  expect(requests.some((path) => path.startsWith("POST /api/short-form/1/operations/"))).toBeTruthy();
});

test("short-form page captures a real rendered-short state on desktop and phone", async ({ page }) => {
  await mockApi(page);
  await page.goto("/short-form");
  await expect(page.getByTestId("short-form-workspace")).toBeVisible();
  await page.getByRole("tab", { name: "Rendered shorts", exact: true }).click();
  await expect(page.locator("video")).toBeVisible();
  await page.screenshot({ path: "../docs/ui/shorts-view-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "../docs/ui/shorts-view-phone.png", fullPage: true });
});
