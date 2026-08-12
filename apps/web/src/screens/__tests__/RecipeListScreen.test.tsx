import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

import type { RecipeQueryItem } from "../../api/catalog";
import { RecipeListScreen } from "../RecipeListScreen";

let mockRouteParams: Record<string, string | string[] | undefined> = {};
const mockReplaceRoute = jest.fn();
jest.mock("expo-router", () => ({
  useLocalSearchParams: () => mockRouteParams,
  useRouter: () => ({ replace: mockReplaceRoute }),
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({
    theme: {
      colors: {
        canvas: "#f7fff9", surface: "#fff", elevatedSurface: "#fff", text: "#10231c", mutedText: "#527060",
        border: "#d0e5d6", accent: "#2d6a4f", accentHover: "#1b4332", accentContrast: "#fff", success: "#2d6a4f",
        warning: "#b7791f", danger: "#b42318", focusRing: "#40916c", scrim: "rgba(0,0,0,.4)",
      },
      spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, "2xl": 48 },
      sizing: { control: 44, icon: 24, touchTarget: 48 },
      radii: { sm: 8, md: 12, lg: 16, pill: 999 },
      type: { caption: 12, body: 15, subtitle: 18, heading: 28, display: 54 },
      shadows: { raised: {} },
    },
  }),
}));

const recipe: RecipeQueryItem = {
  recipe: {
    id: "recipe-1", title: "Lemon pasta", sourceUrl: null, servings: 4,
    prepMinutes: 10, cookMinutes: 15, totalMinutes: 25, ingredients: [], instructions: [], tags: ["pasta"], rating: 4,
  },
  match: null,
};
const secondRecipe: RecipeQueryItem = { ...recipe, recipe: { ...recipe.recipe, id: "recipe-2", title: "Tomato risotto" } };
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((next) => { resolve = next; }); return { promise, resolve }; }

function catalogWith(listRecipes: jest.Mock) {
  return { listRecipes } as unknown as React.ComponentProps<typeof RecipeListScreen>["catalog"];
}

const actions = {
  onOpenDetail: jest.fn(), onCreate: jest.fn(), onImport: jest.fn(), onLogout: jest.fn(), onUnauthorized: jest.fn(),
};

beforeEach(() => {
  jest.clearAllMocks();
  mockRouteParams = {};
});

test("shows stable skeleton slots while the first library request is loading", async () => {
  const listRecipes = jest.fn(() => new Promise<never>(() => undefined));
  const screen = await render(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);

  expect(screen.getAllByTestId("recipe-card-skeleton")).toHaveLength(3);
});

test("shows an actionable empty library", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await render(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);

  await waitFor(() => expect(screen.getByText("Your recipe library is empty.")).toBeTruthy());
});

test("presents a safe retry action after a library error", async () => {
  const listRecipes = jest.fn()
    .mockRejectedValueOnce(new Error("internal provider URL and payload"))
    .mockResolvedValueOnce({ items: [], nextCursor: null });
  const screen = await render(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);

  await waitFor(() => expect(screen.getByText("We couldn't load your recipes. Please try again.")).toBeTruthy());
  expect(screen.queryByText("internal provider URL and payload")).toBeNull();
  fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.getByText("Your recipe library is empty.")).toBeTruthy());
});

test("switches populated results between card and semantic list views", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null });
  const screen = await render(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);

  await waitFor(() => expect(screen.getByTestId("recipe-results-card")).toBeTruthy());
  fireEvent.press(screen.getByRole("button", { name: "List view" }));
  await waitFor(() => expect(screen.getByTestId("recipe-results-list")).toBeTruthy());
});

test("debounces search and serializes normalized library filters into the route", async () => {
  jest.useFakeTimers();
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await render(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);

  fireEvent.changeText(screen.getByLabelText("Search recipes"), "  Tomato   soup ");
  jest.advanceTimersByTime(299);
  expect(mockReplaceRoute).not.toHaveBeenCalled();
  jest.advanceTimersByTime(1);
  expect(mockReplaceRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { text: "tomato soup" } });
  jest.useRealTimers();
});

test("restores every normalized API filter and ordered sort from direct and back-forward route state", async () => {
  mockRouteParams = {
    text: "  tomato   soup ", requiredIngredient: ["Tomato", "basil", "tomato"], availableIngredient: "onion",
    requiredTag: ["Vegan", "quick"], preferredTag: "family", maxTotalMinutes: "30", minRating: "4", ratingState: "rated",
    sort: ["rating:desc", "totalMinutes:asc"],
  };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await render(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);

  await waitFor(() => expect(listRecipes).toHaveBeenCalledWith(expect.objectContaining({
    text: "tomato soup", requiredIngredient: ["basil", "tomato"], availableIngredient: ["onion"],
    requiredTag: ["quick", "vegan"], preferredTag: ["family"], maxTotalMinutes: 30, minRating: 4,
    ratingState: "rated", sort: ["rating:desc", "totalMinutes:asc"], limit: 20,
  }), expect.anything()));

  mockRouteParams = { text: "risotto", ratingState: "unrated" };
  screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await waitFor(() => expect(listRecipes).toHaveBeenLastCalledWith(expect.objectContaining({ text: "risotto", ratingState: "unrated" }), expect.anything()));
});

test("appends a cursor page without losing or duplicating prior recipes", async () => {
  const next = deferred<{ items: RecipeQueryItem[]; nextCursor: string | null }>();
  const listRecipes = jest.fn().mockResolvedValueOnce({ items: [recipe], nextCursor: "cursor-2" }).mockReturnValueOnce(next.promise);
  const screen = await render(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Load more recipes" })).toBeTruthy());
  fireEvent.press(screen.getByRole("button", { name: "Load more recipes" }));
  expect(listRecipes).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: "cursor-2" }), expect.anything());
  await act(async () => next.resolve({ items: [recipe, secondRecipe], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("2 recipes loaded")).toBeTruthy());
});

test("does not update or surface errors after an in-flight library request unmounts", async () => {
  const stale = deferred<{ items: RecipeQueryItem[]; nextCursor: string | null }>();
  const listRecipes = jest.fn().mockReturnValueOnce(stale.promise);
  const screen = await render(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  screen.unmount();
  await act(async () => stale.resolve({ items: [recipe], nextCursor: null }));
  expect(actions.onUnauthorized).not.toHaveBeenCalled();
});
