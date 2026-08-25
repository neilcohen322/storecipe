import { expect, test } from "@playwright/test";

import { assertStablePageQuality, captureConsoleErrors, installApiInterceptions } from "./support";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installApiInterceptions(page);
});

test("renders privacy and terms routes", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/privacy");
  await expect(page.getByRole("heading", { name: "Privacy policy" })).toBeVisible();
  await page.goto("/terms");
  await expect(page.getByRole("heading", { name: "Terms of service" })).toBeVisible();
  await assertStablePageQuality(page, errors);
});

test("account deletion controls are reachable", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/account");
  await expect(page.getByRole("button", { name: "Delete my account" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Export cookbook" })).toBeVisible();
  await assertStablePageQuality(page, errors);
});
