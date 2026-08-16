import type { RecipeCreate } from "../api/catalog";

export function parseRecipeLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

/** Fingerprint normalized raw ingredient lines for Ingestion idempotency. */
export function fingerprintIngredientNormalization(rawLines: readonly string[]): string {
  return JSON.stringify({ ingredients: [...rawLines] });
}

/** Fingerprint the exact reviewed create payload, not raw form text. */
export function fingerprintRecipeCreate(payload: RecipeCreate): string {
  return JSON.stringify({
    title: payload.title,
    sourceUrl: payload.sourceUrl ?? null,
    servings: payload.servings ?? null,
    prepMinutes: payload.prepMinutes ?? null,
    cookMinutes: payload.cookMinutes ?? null,
    totalMinutes: payload.totalMinutes ?? null,
    ingredients: payload.ingredients.map((ingredient) => ({
      rawText: ingredient.rawText,
      name: ingredient.name,
      canonicalName: ingredient.canonicalName,
      quantity: ingredient.quantity ?? null,
      unit: ingredient.unit ?? null,
    })),
    instructions: payload.instructions,
    tags: payload.tags ?? [],
  });
}
