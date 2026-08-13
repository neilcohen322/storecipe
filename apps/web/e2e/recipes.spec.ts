import { expect, test } from "@playwright/test";

import { assertStablePageQuality, captureConsoleErrors, installApiInterceptions } from "./support";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installApiInterceptions(page);
});

test("searches, opens a recipe, and saves a rating", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/recipes");
  await page.getByLabel("Search recipes").fill("tomato pasta");
  await expect(page).toHaveURL(/text=tomato(?:%20|\+)pasta/, { timeout: 3_000 });
  await page.getByRole("button", { name: "Open Weeknight tomato pasta" }).click();
  await expect(page.getByRole("heading", { name: "Weeknight tomato pasta" })).toBeVisible();
  await page.getByRole("button", { name: "Rate 5 out of 5" }).click();
  await expect(page.getByText("5 out of 5")).toBeVisible();
  await assertStablePageQuality(page, errors);
});

test("validates and creates a recipe", async ({ page }, testInfo) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/recipes/new");
  const submit = page.getByRole("button", { name: "Create recipe" });
  await submit.click();
  await expect(page.getByText("Title is required.")).toBeVisible();
  await expect(page.getByText("Add at least one ingredient.")).toBeVisible();
  await expect(page.getByText("Add at least one instruction.")).toBeVisible();
  await page.getByLabel("Title").fill("Browser baked pasta");
  await page.getByLabel("Ingredients").fill("300 g pasta\n2 cups tomatoes");
  await page.getByLabel("Instructions").fill("Boil pasta\nBake with tomatoes");
  await submit.click();
  await expect(page).toHaveURL(/\/recipes\/created-e2e-recipe$/);
  await expect(page.getByRole("heading", { name: "Browser baked pasta" })).toBeVisible();
  if (testInfo.project.name.startsWith("compact")) await expect(page.getByTestId("create-recipe-sticky-submit")).toHaveCount(0);
  await assertStablePageQuality(page, errors);
});

test("supports a fresh direct entry to a dynamic recipe route", async ({ browser, baseURL }) => {
  const context = await browser.newContext({ storageState: ".playwright/auth.json", reducedMotion: "reduce" });
  const page = await context.newPage();
  const errors = captureConsoleErrors(page);
  await installApiInterceptions(page);
  await page.goto(`${baseURL}/recipes/recipe-weeknight-pasta`);
  await expect(page.getByRole("heading", { name: "Weeknight tomato pasta" })).toBeVisible();
  await assertStablePageQuality(page, errors);
  await context.close();
});
