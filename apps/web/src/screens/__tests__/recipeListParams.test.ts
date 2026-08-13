import {
  applyCanonicalSelections,
  normalizeRecipeListParams,
  sameStringList,
  serializeRecipeListParams,
} from "../recipeListParams";

test("keeps comma-containing ingredient params atomic", () => {
  expect(normalizeRecipeListParams({ requiredIngredient: "salt, divided" }).requiredIngredient).toEqual(["salt, divided"]);
  expect(normalizeRecipeListParams({ requiredIngredient: ["salt, divided", "tomato"] }).requiredIngredient).toEqual(["salt, divided", "tomato"]);
});

test("preserves ingredient and tag route strings exactly until resolution", () => {
  expect(normalizeRecipeListParams({ requiredIngredient: "Straße" }).requiredIngredient).toEqual(["Straße"]);
  expect(normalizeRecipeListParams({ requiredIngredient: "  tomato  " }).requiredIngredient).toEqual(["  tomato  "]);
  expect(normalizeRecipeListParams({ requiredTag: "Weeknight" }).requiredTag).toEqual(["Weeknight"]);
});

test("omits inactive duration and rating params", () => {
  expect(serializeRecipeListParams(normalizeRecipeListParams({}))).toEqual({});
  expect(serializeRecipeListParams(normalizeRecipeListParams({ maxTotalMinutes: "90", minRating: "1" }))).toMatchObject({
    maxTotalMinutes: "90",
    minRating: "1",
  });
});

test("rewrites URL names to unique normalizedName values", () => {
  const params = normalizeRecipeListParams({ requiredIngredient: ["Straße", "tomato"] });
  const next = applyCanonicalSelections(params, {
    ingredients: [
      { requestedName: "Straße", normalizedName: "strasse", observed: true },
      { requestedName: "tomato", normalizedName: "tomato", observed: true },
    ],
    tags: [],
  });
  expect(next.requiredIngredient).toEqual(["strasse", "tomato"]);
  expect(sameStringList(next.requiredIngredient ?? [], ["strasse", "tomato"])).toBe(true);
});

test("unrated clears minRating and dropping coverage context drops coverage sorts", () => {
  const unrated = normalizeRecipeListParams({ minRating: "4", ratingState: "unrated" });
  expect(unrated.minRating).toBeUndefined();
  const droppedIngredient = normalizeRecipeListParams({
    sort: ["ingredientCoverage:desc", "title:asc"],
  });
  expect(droppedIngredient.sort).toEqual(["title:asc"]);
  const droppedTag = normalizeRecipeListParams({
    sort: ["tagCoverage:desc"],
  });
  expect(droppedTag.sort).toEqual(["updatedAt:desc"]);
});
