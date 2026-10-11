import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

const json = (body: unknown, status = 200) => ({
  status,
  contentType: "application/json",
  body: JSON.stringify(body),
});

test.describe("Access token login", () => {
  test("an unauthenticated visitor is sent to /login and signs in", async ({ page }) => {
    await mockApi(page);
    let signedIn = false;
    // last-registered route wins, so these come after mockApi
    await page.route("**/api/auth/session", (r) =>
      r.fulfill(json({ authenticated: signedIn, auth_required: true })),
    );
    await page.route("**/api/auth/login", async (r) => {
      const { token } = r.request().postDataJSON();
      if (token !== "right-token") return r.fulfill(json({ detail: "Wrong access token" }, 401));
      signedIn = true;
      return r.fulfill(json({ authenticated: true, auth_required: true }));
    });

    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

    await page.getByLabel("Access token").fill("nope");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Wrong access token")).toBeVisible();

    await page.getByLabel("Access token").fill("right-token");
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByText("Total Cases")).toBeVisible();
  });

  test("an expired session (401 from the API) returns to /login", async ({ page }) => {
    await mockApi(page);
    await page.route("**/api/dashboard", (r) => r.fulfill(json({ detail: "Not authenticated" }, 401)));
    await page.goto("/");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("no login is needed when the server has no access token", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByText("Total Cases")).toBeVisible();
    await expect(page).not.toHaveURL(/login/);
  });
});
