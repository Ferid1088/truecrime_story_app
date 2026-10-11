import { test, expect } from "./test-base";
import { mockApi } from "./fixtures";

test.describe("Database backup card", () => {
  test("back up now needs a folder, then reports the written file", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    const button = page.getByRole("button", { name: "Back up now" });
    await expect(page.getByText("no backup yet")).toBeVisible();
    await expect(button).toBeDisabled();

    await page.getByLabel(/Backup folder/).fill("/Volumes/MyDrive/tc");
    await expect(button).toBeEnabled();
    await button.click();
    await expect(page.getByRole("status")).toContainText("truecrime-backup-20261011-220000.db");
    await expect(page.getByLabel("Recent backups").getByText("ok")).toBeVisible();
    await expect(page.getByText("no backup yet")).toHaveCount(0);
  });

  test("an unplugged drive shows the reason, not a silent success", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await page.getByLabel(/Backup folder/).fill("/Volumes/unplugged");
    await page.getByRole("button", { name: "Back up now" }).click();
    await expect(page.getByRole("status")).toContainText("Is the drive connected");
    await expect(page.getByLabel("Recent backups").getByText("failed")).toBeVisible();
  });

  test("the schedule is saved with interval, start time and retention", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    const saved = page.waitForRequest((r) => r.url().endsWith("/api/backup/settings") && r.method() === "PUT");
    await page.getByLabel(/Backup folder/).fill("/Volumes/MyDrive/tc");
    await page.getByLabel("Automatic backups").check();
    await page.getByLabel("Every (hours)").fill("6");
    await page.getByLabel("Starting at").fill("03:30");
    await page.getByLabel(/Keep last/).fill("10");
    await page.getByRole("button", { name: "Save schedule" }).click();
    expect((await saved).postDataJSON()).toEqual({
      enabled: true,
      dir: "/Volumes/MyDrive/tc",
      every_hours: 6,
      start_time: "03:30",
      keep_last: 10,
    });
    await expect(page.getByRole("status")).toHaveText("Saved.");
    await expect(page.getByText(/Next automatic backup/)).toBeVisible();
  });
});
