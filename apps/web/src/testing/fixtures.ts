import type { ImportJob } from "../api/ingestion";
import type { Recipe, RecipeFacetPage, RecipeFacetSelectionsRequest, RecipeFacetSelectionsResponse, RecipeQueryPage } from "../api/catalog";

export const fixtureRecipe: Recipe = {
  id: "recipe-weeknight-pasta",
  title: "Weeknight tomato pasta",
  sourceUrl: null,
  servings: 4,
  prepMinutes: 10,
  cookMinutes: 20,
  totalMinutes: 30,
  ingredients: [
    { rawText: "2 cups tomatoes", name: "tomatoes", canonicalName: "tomatoes", quantity: 2, unit: "cups" },
    { rawText: "300 g pasta", name: "pasta", canonicalName: "pasta", quantity: 300, unit: "g" },
  ],
  instructions: ["Boil the pasta.", "Simmer the tomatoes and combine."],
  tags: ["weeknight", "vegetarian"],
  rating: 4,
  coverImage: null,
};

export const fixtureRecipePage: RecipeQueryPage = {
  items: [fixtureRecipe],
  nextCursor: null,
};

export const fixtureRecipeFacets: RecipeFacetPage = {
  ingredients: ["tomatoes", "pasta"],
  ingredientNextCursor: null,
  tags: ["weeknight", "vegetarian"],
  tagNextCursor: null,
  totalMinutes: { min: 10, max: 60 },
  rating: { min: 1, max: 5 },
  ratingState: ["any", "rated", "unrated"],
  sort: ["rating:asc", "rating:desc", "totalMinutes:asc", "totalMinutes:desc", "createdAt:asc", "createdAt:desc", "updatedAt:asc", "updatedAt:desc", "title:asc", "title:desc"],
};

export function fixtureRecipeFacetSelections(body: RecipeFacetSelectionsRequest = {}): RecipeFacetSelectionsResponse {
  const resolved = (names: string[] = []) =>
    names.map((requestedName) => ({
      requestedName,
      resolvedName: requestedName,
      status: "observed" as const,
    }));
  return { ingredients: resolved(body.ingredients), tags: resolved(body.tags) };
}

export function fixtureImportJob(status: ImportJob["status"]): ImportJob {
  return {
    id: "import-e2e-job",
    status,
    attemptCount: status === "queued" ? 0 : 1,
    createdRecipeId: status === "completed" ? fixtureRecipe.id : null,
    errorCategory: null,
    cancellationRequested: false,
    hasCandidate: false,
  };
}
