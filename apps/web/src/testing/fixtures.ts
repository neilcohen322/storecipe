import type { ImportJob } from "../api/ingestion";
import type { Recipe, RecipeQueryPage } from "../api/catalog";

export const fixtureRecipe: Recipe = {
  id: "recipe-weeknight-pasta",
  title: "Weeknight tomato pasta",
  sourceUrl: null,
  servings: 4,
  prepMinutes: 10,
  cookMinutes: 20,
  totalMinutes: 30,
  ingredients: [
    { rawText: "2 cups tomatoes", name: "tomatoes", quantity: 2, unit: "cups" },
    { rawText: "300 g pasta", name: "pasta", quantity: 300, unit: "g" },
  ],
  instructions: ["Boil the pasta.", "Simmer the tomatoes and combine."],
  tags: ["weeknight", "vegetarian"],
  rating: 4,
};

export const fixtureRecipePage: RecipeQueryPage = {
  items: [{ recipe: fixtureRecipe, match: null }],
  nextCursor: null,
};

export function fixtureImportJob(status: ImportJob["status"]): ImportJob {
  return {
    id: "import-e2e-job",
    status,
    attemptCount: status === "queued" ? 0 : 1,
    createdRecipeId: status === "completed" ? fixtureRecipe.id : null,
    errorCategory: null,
    cancellationRequested: false,
  };
}
