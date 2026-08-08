export function parseRecipeLines(value: string): string[] {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

export type RecipeCreateFingerprintInput = {
  title: string;
  ingredients: ReadonlyArray<{ rawText: string; name: string }>;
  instructions: readonly string[];
};

/** Fingerprint the normalized create payload, not the raw form text. */
export function fingerprintRecipeCreate(payload: RecipeCreateFingerprintInput): string {
  return JSON.stringify({
    title: payload.title,
    ingredients: payload.ingredients,
    instructions: payload.instructions,
  });
}
