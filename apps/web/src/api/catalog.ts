import type { createApiClient } from "./client";

export type Ingredient = {
  rawText: string;
  name: string;
  canonicalName: string;
  quantity?: number | null;
  unit?: string | null;
};

export type RecipeCreateIngredient = {
  rawText: string;
  name: string;
  canonicalName: string;
  quantity?: number | null;
  unit?: string | null;
};

export type CoverImage = {
  url: string;
  etag: string;
  byteSize: number;
  contentType: "image/webp";
};

export type CoverImageResponse = {
  blob: Blob | null;
  etag: string | null;
  notModified: boolean;
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
  coverImage: CoverImage | null;
};

export type RecipeCreate = {
  title: string;
  sourceUrl?: string | null;
  servings?: number | null;
  prepMinutes?: number | null;
  cookMinutes?: number | null;
  totalMinutes?: number | null;
  ingredients: RecipeCreateIngredient[];
  instructions: string[];
  tags?: string[];
};

export type RecipeQueryPage = {
  items: Recipe[];
  nextCursor: string | null;
};

export type Rating = {
  value: number;
};

export type RecipeSort =
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
  ingredient?: string[];
  tag?: string[];
  maxTotalMinutes?: number | null;
  minRating?: number | null;
  ratingState?: "any" | "rated" | "unrated";
  sort?: RecipeSort[];
  cursor?: string | null;
  limit?: number;
};

export type ListRecipesOptions = {
  signal?: AbortSignal;
};

export type RecipeFacetBounds = {
  min: number;
  max: number;
};

export type RecipeFacetPage = {
  ingredients: string[];
  ingredientNextCursor: string | null;
  tags: string[];
  tagNextCursor: string | null;
  totalMinutes: RecipeFacetBounds | null;
  rating: RecipeFacetBounds;
  ratingState: ("any" | "rated" | "unrated")[];
  sort: RecipeSort[];
};

export type RecipeFacetBrowseParams = {
  ingredientLimit?: number;
  tagLimit?: number;
  ingredientCursor?: string | null;
  tagCursor?: string | null;
  ingredientQ?: string | null;
  tagQ?: string | null;
};

export type RecipeFacetSelection = {
  requestedName: string;
  resolvedName: string | null;
  status: "observed" | "unavailable" | "ambiguous";
};

export type RecipeFacetSelectionsRequest = {
  ingredients?: string[];
  tags?: string[];
};

export type RecipeFacetSelectionsResponse = {
  ingredients: RecipeFacetSelection[];
  tags: RecipeFacetSelection[];
};

export type RecipeFacetOptions = {
  signal?: AbortSignal;
};

export function buildRecipeQueryPath(params?: ListRecipesParams): string {
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

export function buildRecipeFacetPath(params?: RecipeFacetBrowseParams): string {
  if (!params) {
    return "/v1/recipe-facets";
  }

  const search = new URLSearchParams();
  for (const key of ["ingredientLimit", "tagLimit", "ingredientCursor", "tagCursor", "ingredientQ", "tagQ"] as const) {
    const value = params[key];
    if (value === undefined || value === null) {
      continue;
    }
    if (typeof value === "string" && value === "") {
      continue;
    }
    search.append(key, String(value));
  }

  const query = search.toString();
  return query ? `/v1/recipe-facets?${query}` : "/v1/recipe-facets";
}

export function parseCoverImage(value: unknown): CoverImage | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  const cover = value as { url?: unknown; etag?: unknown; byteSize?: unknown; contentType?: unknown };
  if (typeof cover.url !== "string" || typeof cover.etag !== "string" || typeof cover.byteSize !== "number") {
    return null;
  }
  return {
    url: cover.url,
    etag: cover.etag,
    byteSize: cover.byteSize,
    contentType: "image/webp",
  };
}

function parseRecipe(value: unknown): Recipe {
  const recipe = value as Recipe;
  return {
    ...recipe,
    coverImage: parseCoverImage((value as { coverImage?: unknown }).coverImage),
  };
}

export function createCatalogApi(client: ReturnType<typeof createApiClient>) {
  const listRecipes = async (params?: ListRecipesParams, options: ListRecipesOptions = {}): Promise<RecipeQueryPage> => {
    const page = await client.getJson<unknown>(buildRecipeQueryPath(params), options);
    if (!page || typeof page !== "object" || !Array.isArray((page as { items?: unknown }).items)) {
      throw new Error("Invalid recipe library response");
    }
    return {
      items: ((page as { items: unknown[] }).items).map(parseRecipe),
      nextCursor: typeof (page as { nextCursor?: unknown }).nextCursor === "string" ? (page as { nextCursor: string }).nextCursor : null,
    };
  };

  const getRecipe = async (id: string): Promise<Recipe> =>
    parseRecipe(await client.getJson<unknown>(`/v1/recipes/${id}`));

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
    return parseRecipe(await response.json());
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

  const uploadCoverImage = async (recipeId: string, image: Blob): Promise<CoverImage> => {
    const body = new FormData();
    body.append("image", image);
    const response = await client.request(`/v1/recipes/${recipeId}/cover-image`, {
      method: "PUT",
      body,
    });
    return parseCoverImage(await response.json()) as CoverImage;
  };

  const getCoverImage = async (
    recipeId: string,
    options: { etag?: string; signal?: AbortSignal } = {},
  ): Promise<CoverImageResponse> => {
    const headers = new Headers();
    if (options.etag) {
      const tag = options.etag.startsWith('"') ? options.etag : `"${options.etag}"`;
      headers.set("If-None-Match", tag);
    }
    const response = await client.request(`/v1/recipes/${recipeId}/cover-image`, {
      method: "GET",
      headers,
      signal: options.signal,
      allowStatuses: [304],
    });
    if (response.status === 304) {
      return { blob: null, etag: options.etag ?? null, notModified: true };
    }
    const etagHeader = response.headers.get("ETag");
    const etag = etagHeader ? etagHeader.replaceAll('"', "") : null;
    return { blob: await response.blob(), etag, notModified: false };
  };

  const deleteCoverImage = async (recipeId: string): Promise<void> => {
    await client.request(`/v1/recipes/${recipeId}/cover-image`, { method: "DELETE" });
  };

  const listRecipeFacets = async (
    params?: RecipeFacetBrowseParams,
    options: RecipeFacetOptions = {},
  ): Promise<RecipeFacetPage> => {
    const page = await client.getJson<unknown>(buildRecipeFacetPath(params), options);
    if (!page || typeof page !== "object") {
      throw new Error("Invalid recipe facet response");
    }
    return page as RecipeFacetPage;
  };

  const resolveRecipeFacetSelections = async (
    body: RecipeFacetSelectionsRequest,
    options: RecipeFacetOptions = {},
  ): Promise<RecipeFacetSelectionsResponse> => {
    const response = await client.request("/v1/recipe-facet-selections", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: options.signal,
    });
    return (await response.json()) as RecipeFacetSelectionsResponse;
  };

  return {
    listRecipes,
    getRecipe,
    createRecipe,
    putRating,
    uploadCoverImage,
    getCoverImage,
    deleteCoverImage,
    listRecipeFacets,
    resolveRecipeFacetSelections,
  };
}
