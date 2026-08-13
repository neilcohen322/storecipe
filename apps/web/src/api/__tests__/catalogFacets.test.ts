import { buildRecipeFacetPath } from "../catalog";

test("buildRecipeFacetPath serializes browse params without recipe query fields", () => {
  const path = buildRecipeFacetPath({ ingredientQ: "tom", ingredientLimit: 10 });
  expect(path).toBe("/v1/recipe-facets?ingredientLimit=10&ingredientQ=tom");
  expect(path).not.toContain("requiredIngredient");
});
