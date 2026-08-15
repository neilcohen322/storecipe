import { buildRecipeFacetPath, buildRecipeQueryPath } from "../catalog";

test("buildRecipeFacetPath serializes browse params without recipe query fields", () => {
  const path = buildRecipeFacetPath({ ingredientQ: "tom", ingredientLimit: 10 });
  expect(path).toBe("/v1/recipe-facets?ingredientLimit=10&ingredientQ=tom");
  expect(path).not.toContain("requiredIngredient");
  expect(path).not.toContain("ingredient=");
});

test("buildRecipeQueryPath serializes repeated ingredient and tag values", () => {
  const path = buildRecipeQueryPath({
    ingredient: ["egg", "flour"],
    tag: ["quick", "vegetarian"],
  });
  expect(path).toBe("/v1/recipes?ingredient=egg&ingredient=flour&tag=quick&tag=vegetarian");
});

test("buildRecipeQueryPath omits empty lanes and old coverage buckets", () => {
  const path = buildRecipeQueryPath({
    text: "soup",
    ingredient: [],
    tag: [],
    sort: ["rating:desc"],
  });
  expect(path).toBe("/v1/recipes?text=soup&sort=rating%3Adesc");
  expect(path).not.toContain("requiredIngredient");
  expect(path).not.toContain("availableIngredient");
  expect(path).not.toContain("requiredTag");
  expect(path).not.toContain("preferredTag");
  expect(path).not.toContain("ingredientCoverage");
});
