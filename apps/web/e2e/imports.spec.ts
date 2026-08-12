import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { captureConsoleErrors, expectNoConsoleErrors, installApiInterceptions } from "./support";

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
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter((violation) => ["critical", "serious"].includes(violation.impact ?? ""))).toEqual([]);
  expectNoConsoleErrors(errors);
});
