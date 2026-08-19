import {
  applyCanonicalSelections,
  normalizeRecipeListParams,
  sameStringList,
  serializeRecipeListParams,
} from "../recipeListParams";

test("keeps comma-containing ingredient params atomic and repeats values as a list", () => {
  expect(normalizeRecipeListParams({ ingredient: "salt, divided" }).ingredient).toEqual(["salt, divided"]);
  expect(normalizeRecipeListParams({ ingredient: ["salt, divided", "tomato"] }).ingredient).toEqual(["salt, divided", "tomato"]);
  expect(normalizeRecipeListParams({ tag: ["quick", "vegan"] }).tag).toEqual(["quick", "vegan"]);
});

test("preserves ingredient and tag route strings exactly until resolution", () => {
  expect(normalizeRecipeListParams({ ingredient: "Straße" }).ingredient).toEqual(["Straße"]);
  expect(normalizeRecipeListParams({ ingredient: "  tomato  " }).ingredient).toEqual(["  tomato  "]);
  expect(normalizeRecipeListParams({ tag: "Weeknight" }).tag).toEqual(["Weeknight"]);
});

test("caps ingredients at 32 and tags at 16 while keeping first-seen order", () => {
  const ingredients = Array.from({ length: 40 }, (_, index) => `ing-${index}`);
  const tags = Array.from({ length: 20 }, (_, index) => `tag-${index}`);
  const params = normalizeRecipeListParams({ ingredient: ingredients, tag: tags });
  expect(params.ingredient).toHaveLength(32);
  expect(params.tag).toHaveLength(16);
  expect(params.ingredient?.[0]).toBe("ing-0");
  expect(params.ingredient?.[31]).toBe("ing-31");
  expect(params.tag?.[15]).toBe("tag-15");
});

test("ignores old four-lane keys and coverage sorts", () => {
  const params = normalizeRecipeListParams({
    requiredIngredient: "tomato",
    availableIngredient: "onion",
    requiredTag: "quick",
    preferredTag: "family",
    ingredient: "egg",
    tag: "weeknight",
    sort: ["ingredientCoverage:desc", "tagCoverage:asc", "title:asc"],
  });
  expect(params.ingredient).toEqual(["egg"]);
  expect(params.tag).toEqual(["weeknight"]);
  expect(params).not.toHaveProperty("requiredIngredient");
  expect(params).not.toHaveProperty("availableIngredient");
  expect(params).not.toHaveProperty("requiredTag");
  expect(params).not.toHaveProperty("preferredTag");
  expect(params.sort).toEqual(["title:asc"]);
});

test("omits inactive duration and rating params", () => {
  expect(serializeRecipeListParams(normalizeRecipeListParams({}))).toEqual({});
  expect(serializeRecipeListParams(normalizeRecipeListParams({ maxTotalMinutes: "90", minRating: "1" }))).toMatchObject({
    maxTotalMinutes: "90",
    minRating: "1",
  });
  expect(serializeRecipeListParams(normalizeRecipeListParams({ ingredient: "tomato", tag: "quick" }))).toEqual({
    ingredient: ["tomato"],
    tag: ["quick"],
  });
});

test("rewrites URL names to unique resolvedName values", () => {
  const params = normalizeRecipeListParams({ ingredient: ["Straße", "tomato"], tag: ["Weeknight"] });
  const next = applyCanonicalSelections(params, {
    ingredients: [
      { requestedName: "Straße", resolvedName: "strasse", status: "observed" },
      { requestedName: "tomato", resolvedName: "tomato", status: "observed" },
    ],
    tags: [{ requestedName: "Weeknight", resolvedName: "weeknight", status: "observed" }],
  });
  expect(next.ingredient).toEqual(["strasse", "tomato"]);
  expect(next.tag).toEqual(["weeknight"]);
  expect(sameStringList(next.ingredient ?? [], ["strasse", "tomato"])).toBe(true);
});

test("keeps unavailable and ambiguous URL tokens unchanged", () => {
  const params = normalizeRecipeListParams({ ingredient: ["ghost", "ביצה"] });
  const next = applyCanonicalSelections(params, {
    ingredients: [
      { requestedName: "ghost", resolvedName: null, status: "unavailable" },
      { requestedName: "ביצה", resolvedName: null, status: "ambiguous" },
    ],
    tags: [],
  });
  expect(next.ingredient).toEqual(["ghost", "ביצה"]);
});

test("unrated clears minRating", () => {
  const unrated = normalizeRecipeListParams({ minRating: "4", ratingState: "unrated" });
  expect(unrated.minRating).toBeUndefined();
  expect(unrated.ratingState).toBe("unrated");
});
