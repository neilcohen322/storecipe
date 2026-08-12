import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

import { assertNoHorizontalOverflow, captureConsoleErrors, expectNoConsoleErrors, installApiInterceptions } from "./support";

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installApiInterceptions(page);
});

test("renders the responsive shell, restores history, and has no serious accessibility violations", async ({ page }, testInfo) => {
  const errors = captureConsoleErrors(page);
  await page.goto("/recipes?text=pasta&requiredIngredient=tomato&availableIngredient=garlic&requiredTag=weeknight&preferredTag=vegetarian&maxTotalMinutes=30&minRating=4&ratingState=rated&sort=rating%3Adesc");
  await expect(page.getByRole("heading", { name: "Recipes" })).toBeVisible();
  await expect(page.getByLabel("Search recipes")).toHaveValue("pasta");
  await expect(page.getByLabel("Required ingredients")).toHaveValue("tomato");
  await expect(page.getByLabel("Available ingredients")).toHaveValue("garlic");
  await expect(page.getByLabel("Required tags")).toHaveValue("weeknight");
  await expect(page.getByLabel("Preferred tags")).toHaveValue("vegetarian");
  await expect(page.getByLabel("Maximum total minutes")).toHaveValue("30");
  await expect(page.getByLabel("Minimum rating")).toHaveValue("4");
  await expect(page.getByLabel("Sort order")).toHaveValue("rating:desc");

  const compact = testInfo.project.name.startsWith("compact");
  await expect(page.getByTestId(compact ? "app-shell-compact" : testInfo.project.name.startsWith("medium") ? "app-shell-medium" : "app-shell-expanded")).toBeVisible();
  await expect(page.getByTestId(compact ? "bottom-navigation" : "desktop-sidebar")).toBeVisible();
  await assertNoHorizontalOverflow(page);
  expect(await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);

  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter((violation) => ["critical", "serious"].includes(violation.impact ?? ""))).toEqual([]);

  await page.getByRole("button", { name: "Open Weeknight tomato pasta" }).click();
  await expect(page).toHaveURL(/\/recipes\/recipe-weeknight-pasta$/);
  await page.goBack();
  await expect(page).toHaveURL(/text=pasta/);
  await expect(page.getByLabel("Search recipes")).toHaveValue("pasta");
  await page.goForward();
  await expect(page.getByRole("heading", { name: "Weeknight tomato pasta" })).toBeVisible();

  expectNoConsoleErrors(errors);
});

test("supports keyboard navigation with visible focus", async ({ page }) => {
  await page.goto("/recipes");
  await expect(page.getByRole("heading", { name: "Recipes" })).toBeVisible();
  await page.keyboard.press("Tab");
  const focused = page.locator(":focus");
  await expect(focused).toBeVisible();
  await expect(focused).toBeFocused();
  expect(await focused.evaluate((element) => {
    const style = getComputedStyle(element);
    return style.outlineStyle !== "none" || style.boxShadow !== "none";
  })).toBe(true);
});

test("logs out through the responsive navigation", async ({ page }, testInfo) => {
  await page.goto(testInfo.project.name.startsWith("compact") ? "/more" : "/account");
  await page.getByRole("button", { name: /log ?out/i }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("button", { name: "Continue with Google" })).toBeVisible();
});
