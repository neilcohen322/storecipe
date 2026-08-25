import type { createApiClient } from "../client";
import { buildRecipeQueryPath, createCatalogApi } from "../catalog";

test("omits favorite from the query unless it is exactly true", () => {
  expect(buildRecipeQueryPath({})).toBe("/v1/recipes");
  expect(buildRecipeQueryPath({ favorite: true })).toBe("/v1/recipes?favorite=true");
  expect(buildRecipeQueryPath({ text: "soup", favorite: true, limit: 20 })).toBe(
    "/v1/recipes?text=soup&favorite=true&limit=20",
  );
});

test("fills personalization defaults and PATCHes, deletes, and marks cooked", async () => {
  const request = jest.fn(async (_path: string, options: { method?: string } = {}) => ({
    status: options.method === "DELETE" ? 204 : 200,
    json: async () => ({
      id: "recipe-1",
      title: "Soup",
      sourceUrl: null,
      servings: null,
      prepMinutes: null,
      cookMinutes: null,
      totalMinutes: null,
      ingredients: [],
      instructions: [],
      tags: [],
      rating: null,
      coverImage: null,
    }),
  }));
  const catalog = createCatalogApi({ request, getJson: jest.fn() } as unknown as ReturnType<typeof createApiClient>);

  const patched = await catalog.patchRecipe("recipe-1", { favorite: true, personalNotes: null });
  expect(request).toHaveBeenCalledWith("/v1/recipes/recipe-1", expect.objectContaining({
    method: "PATCH",
    body: JSON.stringify({ favorite: true, personalNotes: null }),
  }));
  expect(patched.favorite).toBe(false);
  expect(patched.personalNotes).toBeNull();
  expect(patched.lastCookedAt).toBeNull();

  await catalog.deleteRecipe("recipe-1");
  expect(request).toHaveBeenCalledWith("/v1/recipes/recipe-1", { method: "DELETE" });

  await catalog.markRecipeCooked("recipe-1");
  expect(request).toHaveBeenCalledWith("/v1/recipes/recipe-1/cooked", { method: "POST" });
});
