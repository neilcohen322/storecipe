import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { assertNoSensitiveMediaLeak, assertStablePageQuality, captureConsoleErrors, installApiInterceptions, openRecipeFilters, openRecipeSort, pickCoverImage, submitRecipeSearch } from "./support";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installApiInterceptions(page);
});

test("searches, opens a recipe, and saves a rating", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/recipes");
  await page.getByLabel("Search recipes").fill("tomato pasta");
  await expect(page).not.toHaveURL(/text=/, { timeout: 800 });
  await page.getByLabel("Search recipes").press("Enter");
  await expect(page).toHaveURL(/text=tomato(?:%20|\+)pasta/);
  await page.getByRole("button", { name: "Open Weeknight tomato pasta" }).click();
  await expect(page.getByRole("heading", { name: "Weeknight tomato pasta" })).toBeVisible();
  await page.getByRole("button", { name: "Rate 5 out of 5" }).click();
  await expect(page.getByText("5 out of 5")).toBeVisible();
  await assertStablePageQuality(page, errors);
});

test("validates, reviews, and creates a recipe", async ({ page }, testInfo) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/recipes/new");
  const review = page.getByRole("button", { name: "Review recipe" });
  await review.click();
  await expect(page.getByText("Title is required.")).toBeVisible();
  await expect(page.getByText("Add at least one ingredient.")).toBeVisible();
  await expect(page.getByText("Add at least one instruction.")).toBeVisible();
  await page.getByLabel("Title").fill("Browser baked pasta");
  await page.getByLabel("Ingredients").fill("300 g pasta\n2 cups tomatoes");
  await page.getByLabel("Instructions").fill("Boil pasta\nBake with tomatoes");
  await review.click();
  const save = page.getByRole("button", { name: "Save recipe" });
  await expect(save).toBeVisible();
  await save.click();
  await expect(page).toHaveURL(/\/recipes\/created-e2e-recipe$/);
  await expect(page.getByRole("heading", { name: "Browser baked pasta" })).toBeVisible();
  if (testInfo.project.name.startsWith("compact")) await expect(page.getByTestId("create-recipe-sticky-submit")).toHaveCount(0);
  await assertStablePageQuality(page, errors);
});

test("commits search and atomic filters without debounce", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/recipes");
  await expect(page.getByRole("heading", { name: "Recipes" })).toBeVisible();
  await expect(page.getByText("Required ingredients")).toHaveCount(0);
  await expect(page.getByText("requiredIngredient")).toHaveCount(0);

  await submitRecipeSearch(page, "tomato pasta", "button");
  await expect(page).toHaveURL(/text=tomato(?:%20|\+)pasta/);

  await openRecipeFilters(page);
  await assertStablePageQuality(page, errors);
  const tomatoes = page.getByRole("button", { name: "tomatoes" }).first();
  await expect(tomatoes).toBeVisible();
  await tomatoes.click();
  await expect(page).not.toHaveURL(/ingredient=/);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("dialog", { name: "Filters" })).toBeHidden();
  await expect(page).not.toHaveURL(/ingredient=/);

  await openRecipeFilters(page);
  await expect(page.getByRole("button", { name: "Remove tomatoes" })).toHaveCount(0);
  await page.getByRole("button", { name: "tomatoes" }).first().click();
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(/ingredient=tomatoes/);
  await expect(page.getByRole("button", { name: /^Filters/ })).toHaveAccessibleName("Filters (1)");

  await openRecipeSort(page);
  await assertStablePageQuality(page, errors);
  await page.getByRole("button", { name: "Highest rated" }).click();
  await expect(page).toHaveURL(/sort=rating(?:%3A|:)desc/);
  await expect(page.getByRole("dialog", { name: "Sort" })).toBeHidden();

  const filteredUrl = page.url();
  await page.goBack();
  await expect(page).not.toHaveURL(/sort=/);
  await page.goForward();
  await expect(page).toHaveURL(filteredUrl);

  await openRecipeFilters(page);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("button", { name: /^Filters/ })).toBeFocused();

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

async function fillCreateForm(page: Page): Promise<void> {
  await page.getByLabel("Title").fill("Browser baked pasta");
  await page.getByLabel("Ingredients").fill("300 g pasta\n2 cups tomatoes");
  await page.getByLabel("Instructions").fill("Boil pasta\nBake with tomatoes");
  await page.getByRole("button", { name: "Review recipe" }).click();
  await expect(page.getByRole("button", { name: "Save recipe" })).toBeVisible();
}

test("creates a recipe with a cover, then replaces and removes it", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/recipes/new");
  await fillCreateForm(page);
  await pickCoverImage(page, "Add cover image");
  await expect(page.getByLabel("Selected cover preview")).toBeVisible();
  await page.getByRole("button", { name: "Save recipe" }).click();
  await expect(page).toHaveURL(/\/recipes\/created-e2e-recipe$/);
  await expect(page.getByRole("heading", { name: "Browser baked pasta" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Replace cover image" })).toBeVisible();
  await pickCoverImage(page, "Replace cover image");
  await expect(page.getByRole("button", { name: "Replace cover image" })).toBeVisible();
  await page.getByRole("button", { name: "Remove cover image" }).click();
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(page.getByRole("button", { name: "Add cover image" })).toBeVisible();
  await expect(page.getByLabel("Browser baked pasta recipe placeholder")).toBeVisible();
  await assertNoSensitiveMediaLeak(page);
  await assertStablePageQuality(page, errors);
});

test("retries cover upload after the recipe is already saved", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  let failOnce = true;
  await page.route(/\/v1\/recipes\/[^/]+\/cover-image$/, async (route) => {
    if (route.request().method() === "PUT" && failOnce) {
      failOnce = false;
      return route.fulfill({
        status: 503,
        contentType: "application/problem+json",
        headers: { "Access-Control-Allow-Origin": "*" },
        body: JSON.stringify({
          detail: "Images are temporarily unavailable. Your recipe is safe.",
          errorCategory: "media_unavailable",
        }),
      });
    }
    return route.fallback();
  });
  await page.goto("/recipes/new");
  await fillCreateForm(page);
  await pickCoverImage(page, "Add cover image");
  await page.getByRole("button", { name: "Save recipe" }).click();
  await expect(page.getByText("Recipe saved; image upload failed.")).toBeVisible();
  await page.getByRole("button", { name: "Try image upload again" }).click();
  await expect(page).toHaveURL(/\/recipes\/created-e2e-recipe$/);
  await expect(page.getByRole("button", { name: "Replace cover image" })).toBeVisible();
  await assertNoSensitiveMediaLeak(page);
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter((violation) => ["critical", "serious"].includes(violation.impact ?? ""))).toEqual([]);
  expect(errors.filter((error) => !error.includes("503"))).toEqual([]);
});

test("renders a private cover on the library card and recipe detail", async ({ page }) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/recipes");
  await expect(page.getByTestId("recipe-cover-image").first()).toBeVisible();
  await page.getByRole("button", { name: "Open Weeknight tomato pasta" }).click();
  await expect(page.getByRole("heading", { name: "Weeknight tomato pasta" })).toBeVisible();
  await expect(page.getByTestId("recipe-cover-image")).toBeVisible();
  await expect(page.getByRole("button", { name: "Replace cover image" })).toBeVisible();
  await assertNoSensitiveMediaLeak(page);
  await assertStablePageQuality(page, errors);
});
