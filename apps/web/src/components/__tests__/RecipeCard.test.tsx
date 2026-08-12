import { fireEvent, render } from "@testing-library/react-native";

import type { RecipeQueryItem } from "../../api/catalog";
import { RecipeCard } from "../RecipeCard";

jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({
    theme: {
      colors: {
        surface: "#fff", elevatedSurface: "#fff", text: "#10231c", mutedText: "#527060",
        border: "#d0e5d6", accent: "#2d6a4f", accentContrast: "#fff", success: "#2d6a4f",
        warning: "#b7791f", danger: "#b42318",
      },
      spacing: { xs: 4, sm: 8, md: 16, lg: 24 },
      radii: { sm: 8, md: 12, lg: 16 },
      type: { body: 15, caption: 12, subtitle: 18 },
      shadows: { raised: {} },
    },
  }),
}));

const recipe: RecipeQueryItem = {
  recipe: {
    id: "recipe-1",
    title: "Lemon pasta",
    sourceUrl: null,
    servings: 4,
    prepMinutes: 10,
    cookMinutes: 15,
    totalMinutes: 25,
    ingredients: [],
    instructions: [],
    tags: ["weeknight", "pasta"],
    rating: 4,
  },
  match: null,
};

test("opens a recipe from an accessible stable card with deterministic media", async () => {
  const onOpen = jest.fn();
  const first = await render(<RecipeCard item={recipe} onOpen={onOpen} view="card" />);
  const firstMedia = first.getByTestId("recipe-card-media-recipe-1");
  const initialMediaStyle = firstMedia.props.style;

  fireEvent.press(first.getByRole("button", { name: "Open Lemon pasta" }));

  expect(onOpen).toHaveBeenCalledWith("recipe-1");
  expect(initialMediaStyle).toEqual(expect.arrayContaining([expect.objectContaining({ backgroundColor: "#b7791f" })]));
  expect(first.getByText("25 min · 4/5")).toBeTruthy();
});
