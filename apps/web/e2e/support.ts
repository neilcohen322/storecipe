import { expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { fixtureImportJob, fixtureRecipe, fixtureRecipeFacetSelections, fixtureRecipeFacets, fixtureRecipePage } from "../src/testing/fixtures";

export function captureConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

export function expectNoConsoleErrors(errors: string[]): void {
  expect(errors, errors.join("\n")).toEqual([]);
}

export async function assertStablePageQuality(page: Page, errors: string[]): Promise<void> {
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(accessibility.violations.filter((violation) => ["critical", "serious"].includes(violation.impact ?? ""))).toEqual([]);
  expectNoConsoleErrors(errors);
}

export async function installApiInterceptions(page: Page): Promise<void> {
  let importPoll = 0;
  await page.route(/^http:\/\/localhost:800[01]\/v1\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key" };
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    if (url.pathname === "/v1/recipes" && request.method() === "GET") return route.fulfill({ json: fixtureRecipePage, headers: cors });
    if (url.pathname === "/v1/recipes" && request.method() === "POST") return route.fulfill({ status: 201, json: { ...fixtureRecipe, id: "created-e2e-recipe", title: "Browser baked pasta" }, headers: cors });
    if (/^\/v1\/recipes\/[^/]+$/.test(url.pathname)) return route.fulfill({ json: url.pathname.endsWith("created-e2e-recipe") ? { ...fixtureRecipe, id: "created-e2e-recipe", title: "Browser baked pasta" } : fixtureRecipe, headers: cors });
    if (/^\/v1\/recipes\/[^/]+\/rating$/.test(url.pathname)) return route.fulfill({ json: { value: 5 }, headers: cors });
    if ((url.pathname === "/v1/imports/url" || url.pathname === "/v1/imports/text") && request.method() === "POST") return route.fulfill({ status: 202, json: { jobId: "import-e2e-job", status: "queued" }, headers: cors });
    if (url.pathname === "/v1/imports/import-e2e-job") {
      const statuses = ["queued", "processing", "completed"] as const;
      return route.fulfill({ json: fixtureImportJob(statuses[Math.min(importPoll++, statuses.length - 1)]), headers: cors });
    }
    if (url.pathname === "/v1/recipe-facets" && request.method() === "GET") return route.fulfill({ json: fixtureRecipeFacets, headers: cors });
    if (url.pathname === "/v1/recipe-facet-selections" && request.method() === "POST") {
      const body = (request.postDataJSON() ?? {}) as { ingredients?: string[]; tags?: string[] };
      return route.fulfill({ json: fixtureRecipeFacetSelections(body), headers: cors });
    }
    if (url.pathname === "/v1/ingredient-normalizations" && request.method() === "POST") {
      const body = (request.postDataJSON() ?? {}) as { ingredients?: Array<{ rawText?: string }> };
      return route.fulfill({
        json: {
          ingredients: (body.ingredients ?? []).map((ingredient) => ({
            rawText: ingredient.rawText ?? "",
            name: ingredient.rawText ?? "",
            canonicalName: ingredient.rawText ?? "",
            quantity: null,
            unit: null,
          })),
        },
        headers: cors,
      });
    }
    return route.fulfill({ status: 404, json: { detail: "Unmatched E2E request" }, headers: cors });
  });
}

export async function submitRecipeSearch(page: Page, text: string, method: "enter" | "button" = "enter"): Promise<void> {
  await page.getByLabel("Search recipes").fill(text);
  if (method === "button") await page.getByRole("button", { name: "Search" }).click();
  else await page.getByLabel("Search recipes").press("Enter");
}

export async function openRecipeFilters(page: Page): Promise<void> {
  await page.getByRole("button", { name: /^Filters/ }).click();
  await expect(page.getByRole("dialog", { name: "Filters" })).toBeVisible();
}

export async function openRecipeSort(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Sort" }).click();
  await expect(page.getByRole("dialog", { name: "Sort" })).toBeVisible();
}

export async function assertNoHorizontalOverflow(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}
