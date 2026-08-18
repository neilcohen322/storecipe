import { expect, test } from "@playwright/test";

import { assertStablePageQuality, captureConsoleErrors, installApiInterceptions } from "./support";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installApiInterceptions(page);
});

test("validates an import and presents queued, processing, and completed states", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/imports/new");
  await page.getByRole("button", { name: "Start import" }).click();
  await expect(page.getByText("URL is required.")).toBeVisible();
  await page.getByLabel("Recipe URL").fill("https://example.test/weeknight-pasta");
  await page.getByRole("button", { name: "Start import" }).click();
  await expect(page.getByText("Waiting to start")).toBeVisible();
  await expect(page.getByText("Import in progress")).toBeVisible({ timeout: 4_000 });
  await expect(page.getByText("Your recipe import is complete.")).toBeVisible({ timeout: 7_000 });
  await assertStablePageQuality(page, errors);
});

test("returns to the imports list from a direct new-import entry", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/imports/new");
  await page.getByRole("button", { name: "Back to imports" }).click();
  await expect(page).toHaveURL(/\/imports$/);
  await expect(page.getByRole("heading", { name: "Imports", exact: true })).toBeVisible();
  await assertStablePageQuality(page, errors);
});
