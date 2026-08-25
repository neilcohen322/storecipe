import {
  formatScaledQuantity,
  parseLeadingQuantity,
  scaleFactor,
  scaledIngredientText,
  scaleIngredients,
} from "../ingredientScale";

test("parses mixed numbers such as 1 1/2", () => {
  expect(parseLeadingQuantity("1 1/2 cups flour")).toEqual({
    value: 1.5,
    raw: "1 1/2",
    rest: " cups flour",
  });
});

test("parses unicode fractions and tight units", () => {
  expect(parseLeadingQuantity("½ cup sugar")).toEqual({ value: 0.5, raw: "½", rest: " cup sugar" });
  expect(parseLeadingQuantity("1 ½ cups milk")).toEqual({ value: 1.5, raw: "1 ½", rest: " cups milk" });
  expect(parseLeadingQuantity("200g spaghetti")).toEqual({ value: 200, raw: "200", rest: "g spaghetti" });
});

test("ignores ranges, vague lines, and missing leading quantities", () => {
  expect(parseLeadingQuantity("1-2 cups tomatoes")).toBeNull();
  expect(parseLeadingQuantity("2 to 3 onions")).toBeNull();
  expect(parseLeadingQuantity("to taste salt")).toBeNull();
  expect(parseLeadingQuantity("a pinch of salt")).toBeNull();
  expect(parseLeadingQuantity("about 2 cups flour")).toBeNull();
  expect(parseLeadingQuantity("salt")).toBeNull();
});

test("formats common fractions and trimmed decimals", () => {
  expect(formatScaledQuantity(0.5)).toBe("1/2");
  expect(formatScaledQuantity(1.5)).toBe("1 1/2");
  expect(formatScaledQuantity(0.33)).toBe("1/3");
  expect(formatScaledQuantity(1.25)).toBe("1 1/4");
  expect(formatScaledQuantity(1.11)).toBe("1.11");
  expect(formatScaledQuantity(2)).toBe("2");
});

test("uses servings when the recipe has a positive base", () => {
  expect(scaleFactor({ baseServings: 4, selectedServings: 8, multiplier: 1 })).toBe(2);
  expect(scaleFactor({ baseServings: null, selectedServings: null, multiplier: 0.5 })).toBe(0.5);
});

test("scales mixed leading quantities when structured values agree", () => {
  expect(scaledIngredientText({ rawText: "1 1/2 cups flour", quantity: 1.5 }, 2)).toBe("3 cups flour");
  expect(scaledIngredientText({ rawText: "2 cups tomatoes", quantity: 2 }, 1.25)).toBe("2 1/2 cups tomatoes");
  expect(scaledIngredientText({ rawText: "200g spaghetti", quantity: 200 }, 2)).toBe("400g spaghetti");
});

test("leaves uncertain or disagreeing lines unchanged", () => {
  expect(scaledIngredientText({ rawText: "200g spaghetti" }, 2)).toBe("200g spaghetti");
  expect(scaledIngredientText({ rawText: "2 cups tomatoes", quantity: 3 }, 2)).toBe("2 cups tomatoes");
  expect(scaledIngredientText({ rawText: "to taste salt", quantity: 1 }, 2)).toBe("to taste salt");
  expect(scaledIngredientText({ rawText: "1-2 cups tomatoes", quantity: 1 }, 2)).toBe("1-2 cups tomatoes");
});

test("scales only agreed lines in a list", () => {
  expect(scaleIngredients([
    { rawText: "2 cups tomatoes", quantity: 2 },
    { rawText: "to taste salt" },
  ], 2)).toEqual(["4 cups tomatoes", "to taste salt"]);
});
