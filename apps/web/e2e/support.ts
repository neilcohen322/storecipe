import { expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { fixtureImportJob, fixtureRecipe, fixtureRecipeFacetSelections, fixtureRecipeFacets } from "../src/testing/fixtures";

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

const NodeBuffer = (globalThis as unknown as { Buffer: { from(data: number[]): Uint8Array } }).Buffer;
export const TINY_WEBP = NodeBuffer.from([
  0x52, 0x49, 0x46, 0x46, 0x24, 0x00, 0x00, 0x00, 0x57, 0x45, 0x42, 0x50, 0x56, 0x50, 0x38, 0x20,
  0x18, 0x00, 0x00, 0x00, 0x30, 0x01, 0x00, 0x9d, 0x01, 0x2a, 0x01, 0x00, 0x01, 0x00, 0x03, 0x00,
  0x34, 0x25, 0xa4, 0x00, 0x03, 0x70, 0x00, 0xfe, 0xfb, 0x94, 0x00, 0x00,
]);

export async function pickCoverImage(page: Page, buttonName: string): Promise<void> {
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser"),
    page.getByRole("button", { name: buttonName }).click(),
  ]);
  await chooser.setFiles({
    name: "cover.webp",
    mimeType: "image/webp",
    buffer: TINY_WEBP as never,
  });
}

export async function assertNoSensitiveMediaLeak(page: Page): Promise<void> {
  const text = await page.locator("body").innerText();
  expect(text, text).not.toMatch(/Bearer\s+[A-Za-z0-9._-]+/);
  expect(text, text).not.toMatch(/GOOGLE_APPLICATION_CREDENTIALS|private_key|gs:\/\//);
  expect(text, text).not.toMatch(/Traceback|Object store unavailable/);
}

export async function installApiInterceptions(page: Page): Promise<void> {
  let importPoll = 0;
  let coverVersion = 0;
  const covers = new Map<string, { etag: string; byteSize: number }>();
  covers.set(fixtureRecipe.id, { etag: "0".repeat(64), byteSize: TINY_WEBP.length });
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Idempotency-Key, If-None-Match",
    "Access-Control-Expose-Headers": "ETag",
  };
  const recipeJson = (id: string, title: string) => ({
    ...fixtureRecipe,
    id,
    title,
    coverImage: covers.has(id)
      ? { url: `/v1/recipes/${id}/cover-image`, etag: covers.get(id)!.etag, byteSize: covers.get(id)!.byteSize, contentType: "image/webp" }
      : null,
  });
  await page.route(/^http:\/\/localhost:800[01]\/v1\//, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (request.method() === "OPTIONS") return route.fulfill({ status: 204, headers: cors });
    if (/^\/v1\/recipes\/[^/]+\/cover-image$/.test(url.pathname)) {
      const recipeId = url.pathname.split("/")[3];
      if (request.method() === "PUT") {
        coverVersion += 1;
        const etag = coverVersion.toString(16).padStart(64, "0");
        covers.set(recipeId, { etag, byteSize: TINY_WEBP.length });
        return route.fulfill({ json: { url: url.pathname, etag, byteSize: TINY_WEBP.length, contentType: "image/webp" }, headers: cors });
      }
      if (request.method() === "DELETE") {
        covers.delete(recipeId);
        return route.fulfill({ status: 204, headers: cors });
      }
      const cover = covers.get(recipeId);
      if (!cover) return route.fulfill({ status: 404, json: { errorCategory: "cover_image_not_found" }, headers: cors });
      const quoted = `"${cover.etag}"`;
      const ifNoneMatch = request.headers()["if-none-match"];
      if (ifNoneMatch === quoted || ifNoneMatch === cover.etag) {
        return route.fulfill({
          status: 304,
          body: "",
          headers: { ...cors, ETag: quoted, "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff" },
        });
      }
      return route.fulfill({
        status: 200,
        body: TINY_WEBP,
        headers: { ...cors, "Content-Type": "image/webp", ETag: quoted, "Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff" },
      });
    }
    if (url.pathname === "/v1/recipes" && request.method() === "GET") {
      return route.fulfill({ json: { items: [recipeJson(fixtureRecipe.id, fixtureRecipe.title)], nextCursor: null }, headers: cors });
    }
    if (url.pathname === "/v1/recipes" && request.method() === "POST") {
      return route.fulfill({ status: 201, json: recipeJson("created-e2e-recipe", "Browser baked pasta"), headers: cors });
    }
    if (/^\/v1\/recipes\/[^/]+$/.test(url.pathname)) {
      return route.fulfill({
        json: url.pathname.endsWith("created-e2e-recipe")
          ? recipeJson("created-e2e-recipe", "Browser baked pasta")
          : recipeJson(fixtureRecipe.id, fixtureRecipe.title),
        headers: cors,
      });
    }
    if (/^\/v1\/recipes\/[^/]+\/rating$/.test(url.pathname)) return route.fulfill({ json: { value: 5 }, headers: cors });
    if ((url.pathname === "/v1/imports/url" || url.pathname === "/v1/imports/text") && request.method() === "POST") {
      return route.fulfill({ status: 202, json: { jobId: "import-e2e-job", status: "queued" }, headers: cors });
    }
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
