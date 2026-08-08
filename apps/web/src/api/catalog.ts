import type { createApiClient } from "./client";

export type Ingredient = {
  rawText: string;
  name: string;
  quantity?: number | null;
  unit?: string | null;
};

export type Recipe = {
  id: string;
  title: string;
  sourceUrl: string | null;
  servings: number | null;
  prepMinutes: number | null;
  cookMinutes: number | null;
  totalMinutes: number | null;
  ingredients: Ingredient[];
  instructions: string[];
  tags: string[];
  rating: number | null;
};

export type RecipeCreate = {
  title: string;
  sourceUrl?: string | null;
  servings?: number | null;
  prepMinutes?: number | null;
  cookMinutes?: number | null;
  totalMinutes?: number | null;
  ingredients: Ingredient[];
  instructions: string[];
  tags?: string[];
};

export type RecipeMatch = {
  ingredientCoverage: number | null;
  missingIngredients: string[];
  tagCoverage: number | null;
  matchedPreferredTags: string[];
  missingPreferredTags: string[];
};

export type RecipeQueryItem = {
  recipe: Recipe;
  match: RecipeMatch | null;
};

export type RecipeQueryPage = {
  items: RecipeQueryItem[];
  nextCursor: string | null;
};

export type Rating = {
  value: number;
};

export type RecipeSort =
  | "ingredientCoverage:asc"
  | "ingredientCoverage:desc"
  | "tagCoverage:asc"
  | "tagCoverage:desc"
  | "rating:asc"
  | "rating:desc"
  | "totalMinutes:asc"
  | "totalMinutes:desc"
  | "createdAt:asc"
  | "createdAt:desc"
  | "updatedAt:asc"
  | "updatedAt:desc"
  | "title:asc"
  | "title:desc";

export type ListRecipesParams = {
  text?: string | null;
  requiredIngredient?: string[];
  availableIngredient?: string[];
  requiredTag?: string[];
  preferredTag?: string[];
  maxTotalMinutes?: number | null;
  minRating?: number | null;
  ratingState?: "any" | "rated" | "unrated";
  sort?: RecipeSort[];
  cursor?: string | null;
  limit?: number;
};

function buildRecipeQueryPath(params?: ListRecipesParams): string {
  if (!params) {
    return "/v1/recipes";
  }

  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) {
      continue;
    }
    if (typeof value === "string" && value === "") {
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        if (item === undefined || item === null || item === "") {
          continue;
        }
        search.append(key, item);
      }
      continue;
    }
    search.append(key, String(value));
  }

  const query = search.toString();
  return query ? `/v1/recipes?${query}` : "/v1/recipes";
}

export function createCatalogApi(client: ReturnType<typeof createApiClient>) {
  const listRecipes = (params?: ListRecipesParams): Promise<RecipeQueryPage> =>
    client.getJson<RecipeQueryPage>(buildRecipeQueryPath(params));

  const getRecipe = (id: string): Promise<Recipe> =>
    client.getJson<Recipe>(`/v1/recipes/${id}`);

  const createRecipe = async (
    body: RecipeCreate,
    idempotencyKey: string,
  ): Promise<Recipe> => {
    const response = await client.request("/v1/recipes", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(body),
    });
    return (await response.json()) as Recipe;
  };

  const putRating = async (
    recipeId: string,
    value: 1 | 2 | 3 | 4 | 5,
  ): Promise<Rating> => {
    const response = await client.request(`/v1/recipes/${recipeId}/rating`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value }),
    });
    return (await response.json()) as Rating;
  };

  return { listRecipes, getRecipe, createRecipe, putRating };
}
