import { expect, type Page } from "@playwright/test";

import { fixtureImportJob, fixtureRecipe, fixtureRecipePage } from "../src/testing/fixtures";

export function captureConsoleErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("pageerror", (error) => errors.push(error.message));
  return errors;
}

export function expectNoConsoleErrors(errors: string[]): void {
  expect(errors, errors.join("\n")).toEqual([]);
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
    return route.fulfill({ status: 404, json: { detail: "Unmatched E2E request" }, headers: cors });
  });
}

export async function assertNoHorizontalOverflow(page: Page): Promise<void> {
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
}
