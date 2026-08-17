import { act, cleanup, configure, fireEvent, render, waitFor } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import { ApiError, ApiNetworkError, ApiUnauthorizedError } from "../../api/client";
import type { Recipe, RecipeFacetPage, RecipeFacetSelectionsResponse } from "../../api/catalog";
import { createPaginationRequestGuard, isOfflineError, RecipeListScreen } from "../RecipeListScreen";

let mockRouteParams: Record<string, string | string[] | undefined> = {};
const mockPushRoute = jest.fn();
const mockReplaceRoute = jest.fn();
const mockRouter = { push: mockPushRoute, replace: mockReplaceRoute };
jest.mock("expo-router", () => {
  const { useEffect } = require("react");
  return {
    useLocalSearchParams: () => mockRouteParams,
    useRouter: () => mockRouter,
    useFocusEffect: (callback: () => void | (() => void)) => {
      useEffect(() => {
        const cleanup = callback();
        return typeof cleanup === "function" ? cleanup : undefined;
      }, [callback]);
    },
  };
});

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

const recipe: Recipe = { id: "recipe-1", title: "Lemon pasta", sourceUrl: null, servings: 4, prepMinutes: 10, cookMinutes: 15, totalMinutes: 25, ingredients: [], instructions: [], tags: ["pasta"], rating: 4 };
const secondRecipe: Recipe = { ...recipe, id: "recipe-2", title: "Tomato risotto" };
type Page = { items: Recipe[]; nextCursor: string | null };
type CatalogExtras = { listRecipeFacets?: jest.Mock; resolveRecipeFacetSelections?: jest.Mock };
const hidden = { includeHiddenElements: true } as const;
type Screen = Awaited<ReturnType<typeof render>>;
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((next, fail) => { resolve = next; reject = fail; }); return { promise, resolve, reject }; }
function defaultFacetPage(overrides: Partial<RecipeFacetPage> = {}): RecipeFacetPage {
  return {
    ingredients: ["basil", "tomato"],
    ingredientNextCursor: null,
    tags: ["family", "weeknight"],
    tagNextCursor: null,
    totalMinutes: { min: 15, max: 90 },
    rating: { min: 1, max: 5 },
    ratingState: ["any", "rated", "unrated"],
    sort: ["rating:asc", "rating:desc", "totalMinutes:asc", "totalMinutes:desc", "createdAt:asc", "createdAt:desc", "updatedAt:asc", "updatedAt:desc", "title:asc", "title:desc"],
    ...overrides,
  };
}
function echoResolve(body: { ingredients?: string[]; tags?: string[] } = {}): RecipeFacetSelectionsResponse {
  return {
    ingredients: (body.ingredients ?? []).map((requestedName) => ({ requestedName, resolvedName: requestedName, status: "observed" as const })),
    tags: (body.tags ?? []).map((requestedName) => ({ requestedName, resolvedName: requestedName, status: "observed" as const })),
  };
}
function catalogWith(listRecipes: jest.Mock, extras: CatalogExtras = {}) {
  return {
    listRecipes,
    listRecipeFacets: extras.listRecipeFacets ?? jest.fn().mockResolvedValue(defaultFacetPage()),
    resolveRecipeFacetSelections: extras.resolveRecipeFacetSelections ?? jest.fn().mockImplementation(async (body: { ingredients?: string[]; tags?: string[] }) => echoResolve(body)),
  } as unknown as React.ComponentProps<typeof RecipeListScreen>["catalog"];
}
const actions = { onOpenDetail: jest.fn(), onCreate: jest.fn(), onImport: jest.fn(), onLogout: jest.fn(), onUnauthorized: jest.fn() };
const renderScreen = (listRecipes: jest.Mock, extras: CatalogExtras = {}, layoutMode?: "compact" | "medium" | "expanded") =>
  render(<RecipeListScreen catalog={catalogWith(listRecipes, extras)} {...actions} layoutMode={layoutMode} />);
function filtersTrigger(screen: Screen) {
  const match = screen.getAllByRole("button", hidden).find((button) =>
    String(button.props.accessibilityLabel ?? "").startsWith("Filters"),
  );
  if (!match) throw new Error("Filters trigger not found");
  return match;
}
function filterDialog(screen: Screen) {
  return screen.queryByTestId("filter-dialog", hidden);
}
function expectFiltersClosed(screen: Screen) {
  const dialog = filterDialog(screen);
  expect(dialog == null || dialog.props.visible === false).toBe(true);
}
async function openFilters(screen: Screen) {
  await fireEvent.press(filtersTrigger(screen));
  await waitFor(() => expect(filterDialog(screen)?.props.visible).toBe(true));
  await act(async () => { await Promise.resolve(); });
}
async function closeFilters(screen: Screen, via: "cancel" | "escape" | "back" | "backdrop") {
  if (via === "cancel") await fireEvent.press(dialogButton(screen, "Cancel"));
  else if (via === "backdrop") await fireEvent.press(screen.getByTestId("filter-dialog-backdrop", hidden));
  else await fireEvent(screen.getByTestId("filter-dialog", hidden), "requestClose");
  await waitFor(() => expectFiltersClosed(screen));
}
async function openSort(screen: Screen) {
  await fireEvent.press(screen.getByRole("button", { name: "Sort", ...hidden }));
}
function dialogButton(screen: Screen, name: string | RegExp) {
  return screen.getByRole("button", { name, ...hidden });
}
function queryDialogButton(screen: Screen, name: string | RegExp) {
  return screen.queryByRole("button", { name, ...hidden });
}
async function waitForOption(screen: Screen, name: string) {
  await waitFor(() => expect(dialogButton(screen, name)).toBeTruthy());
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useRealTimers();
  mockRouteParams = {};
  configure({ defaultIncludeHiddenElements: true });
});
afterEach(() => {
  cleanup();
  jest.useRealTimers();
  configure({ defaultIncludeHiddenElements: false });
});

test("shows stable skeleton slots while the first library request is loading", async () => {
  const screen = await renderScreen(jest.fn(() => new Promise<never>(() => undefined)));
  expect(screen.getAllByTestId("recipe-card-skeleton")).toHaveLength(3);
});

test("shows an actionable empty library", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("Your recipe library is empty.")).toBeTruthy());
});

test("presents a safe retry action after a library error", async () => {
  const listRecipes = jest.fn().mockRejectedValueOnce(new Error("internal provider URL and payload")).mockResolvedValueOnce({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(screen.getByText("We couldn't load your recipes. Please try again.")).toBeTruthy());
  expect(screen.queryByText("internal provider URL and payload")).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByText("Your recipe library is empty.")).toBeTruthy());
});

test("shows an offline banner while keeping retry distinct from generic errors", async () => {
  const offline = new ApiNetworkError(Object.assign(new TypeError("fetch failed"), { code: "ERR_NETWORK" }));
  const listRecipes = jest.fn().mockRejectedValueOnce(offline).mockResolvedValueOnce({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(screen.getByText("You’re offline. Check your connection and try again.")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByText("Your recipe library is empty.")).toBeTruthy());
});

test("classifies only closed transport error shapes as offline", () => {
  expect(isOfflineError(new ApiNetworkError(Object.assign(new TypeError("fetch failed"), { code: "ERR_NETWORK" })))).toBe(true);
  expect(isOfflineError(Object.assign(new Error("transport unavailable"), { code: "NETWORK_ERROR" }))).toBe(true);
  expect(isOfflineError(Object.assign(new TypeError("fetch failed"), { cause: { code: "ERR_NETWORK" } }))).toBe(true);
  expect(isOfflineError(new Error("The recipe says network and offline repeatedly"))).toBe(false);
  expect(isOfflineError(Object.assign(new Error("offline"), { code: "VALIDATION_ERROR" }))).toBe(false);
});

test("keeps unauthorized handling distinct from retryable library failures", async () => {
  const screen = await renderScreen(jest.fn().mockRejectedValue(new ApiUnauthorizedError()));
  await waitFor(() => expect(actions.onUnauthorized).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
});

test("switches populated results between card and semantic list views", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null }));
  await waitFor(() => expect(screen.getByTestId("recipe-results-card")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "List view" }));
  expect(screen.getByTestId("recipe-results-list")).toBeTruthy();
});

test("renders Search, Filters, Sort, and view controls without inline pickers", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("Lemon pasta")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Search" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Filters" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Sort" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Card view" })).toBeTruthy();
  expect(screen.queryByText("Ingredients")).toBeNull();
  expect(screen.queryByText("Tags")).toBeNull();
  expect(screen.queryByText("Maximum duration")).toBeNull();
  expect(screen.queryByRole("button", { name: "Recently updated" })).toBeNull();
  expect(screen.queryByRole("button", { name: "tomato" })).toBeNull();
});

test("typing and blur do not push a URL or call listRecipes with the draft", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Search recipes"), "lemon");
  await fireEvent(screen.getByLabelText("Search recipes"), "blur");
  expect(screen.getByLabelText("Search recipes").props.value).toBe("lemon");
  expect(mockPushRoute).not.toHaveBeenCalled();
  expect(mockReplaceRoute).not.toHaveBeenCalled();
  expect(listRecipes).toHaveBeenCalledTimes(1);
  expect(listRecipes.mock.calls[0][0]).not.toEqual(expect.objectContaining({ text: "lemon" }));
});

test("Enter and Search each commit once and an unchanged committed search is a no-op", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Search recipes"), "  Tomato   soup ");
  await fireEvent.press(screen.getByRole("button", { name: "Search" }));
  expect(mockPushRoute).toHaveBeenCalledTimes(1);
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { text: "tomato soup" } });
  expect(mockReplaceRoute).not.toHaveBeenCalled();

  mockRouteParams = { text: "tomato soup" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(2));
  mockPushRoute.mockClear();
  await fireEvent.press(screen.getByRole("button", { name: "Search" }));
  expect(mockPushRoute).not.toHaveBeenCalled();

  await fireEvent.changeText(screen.getByLabelText("Search recipes"), "risotto");
  await fireEvent(screen.getByLabelText("Search recipes"), "submitEditing");
  expect(mockPushRoute).toHaveBeenCalledTimes(1);
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { text: "risotto" } });
});

test("clearing a committed search and pressing Search omits text and reloads once", async () => {
  mockRouteParams = { text: "risotto" };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(1));
  expect(listRecipes.mock.calls[0][0]).toEqual(expect.objectContaining({ text: "risotto" }));
  mockPushRoute.mockClear();
  await fireEvent.changeText(screen.getByLabelText("Search recipes"), "");
  await fireEvent.press(screen.getByRole("button", { name: "Search" }));
  expect(mockPushRoute).toHaveBeenCalledTimes(1);
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: {} });
  mockRouteParams = {};
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(2));
  expect(listRecipes.mock.calls[1][0]).not.toHaveProperty("text");
});

test("updates the search draft immediately and resyncs it from browser navigation", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await fireEvent.changeText(screen.getByLabelText("Search recipes"), "lemon");
  expect(screen.getByLabelText("Search recipes").props.value).toBe("lemon");
  mockRouteParams = { text: "risotto" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await waitFor(() => expect(screen.getByLabelText("Search recipes").props.value).toBe("risotto"));
  expect(mockPushRoute).not.toHaveBeenCalled();
});

test("opening Filters copies committed values and counts active filters", async () => {
  mockRouteParams = { text: "tomato soup", ingredient: ["basil", "tomato"], tag: ["quick", "vegan"], maxTotalMinutes: "30", minRating: "4", ratingState: "rated", sort: ["rating:desc"] };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(screen.getByLabelText("Search recipes").props.value).toBe("tomato soup"));
  expect(screen.getByRole("button", { name: "Filters (7)" })).toBeTruthy();
  await openFilters(screen);
  await waitForOption(screen, "Remove basil");
  expect(dialogButton(screen, "Remove tomato")).toBeTruthy();
  expect(dialogButton(screen, "Remove quick")).toBeTruthy();
  expect(dialogButton(screen, "Remove vegan")).toBeTruthy();
  expect(screen.getByLabelText("30 minutes", hidden)).toBeTruthy();
  expect(dialogButton(screen, "4 and up").props.accessibilityState).toMatchObject({ selected: true });
  expect(dialogButton(screen, "Rated only")).toBeTruthy();
  expect(screen.queryByDisplayValue("rating:desc, totalMinutes:asc")).toBeNull();
  expect(screen.queryByText("requiredIngredient")).toBeNull();
  expect(screen.queryByText("Required ingredients")).toBeNull();
});

test("ingredient tag duration and rating edits update the draft without touching the URL until Apply", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await openFilters(screen);
  await waitForOption(screen, "tomato");
  await fireEvent.press(dialogButton(screen, "tomato"));
  expect(dialogButton(screen, "Remove tomato")).toBeTruthy();
  expect(mockPushRoute).not.toHaveBeenCalled();
  expect(mockReplaceRoute).not.toHaveBeenCalled();
  await fireEvent.press(dialogButton(screen, "Remove tomato"));
  expect(queryDialogButton(screen, "Remove tomato")).toBeNull();
  expect(mockPushRoute).not.toHaveBeenCalled();
  await fireEvent.press(dialogButton(screen, "tomato"));
  await fireEvent.press(dialogButton(screen, "90 minutes"));
  await fireEvent.press(dialogButton(screen, "4 and up"));
  await fireEvent.press(dialogButton(screen, "Rated only"));
  expect(mockPushRoute).not.toHaveBeenCalled();
  expect(mockReplaceRoute).not.toHaveBeenCalled();
  await fireEvent.press(dialogButton(screen, "Apply"));
  expect(mockPushRoute).toHaveBeenCalledTimes(1);
  expect(mockPushRoute).toHaveBeenLastCalledWith({
    pathname: "/recipes",
    params: { ingredient: ["tomato"], maxTotalMinutes: "90", minRating: "4", ratingState: "rated" },
  });
  expectFiltersClosed(screen);
});

test("Cancel, Escape, Back, and backdrop dismiss and restore the committed draft next open", async () => {
  mockRouteParams = { ingredient: "basil" };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await openFilters(screen);
  await waitForOption(screen, "Remove basil");
  await fireEvent.press(dialogButton(screen, "tomato"));
  expect(dialogButton(screen, "Remove tomato")).toBeTruthy();
  await closeFilters(screen, "cancel");
  expect(mockPushRoute).not.toHaveBeenCalled();

  await openFilters(screen);
  await waitForOption(screen, "Remove basil");
  expect(queryDialogButton(screen, "Remove tomato")).toBeNull();
  await fireEvent.press(dialogButton(screen, "tomato"));
  await closeFilters(screen, "escape");
  await openFilters(screen);
  expect(queryDialogButton(screen, "Remove tomato")).toBeNull();
  await fireEvent.press(dialogButton(screen, "tomato"));
  await closeFilters(screen, "back");
  await openFilters(screen);
  expect(queryDialogButton(screen, "Remove tomato")).toBeNull();
  await fireEvent.press(dialogButton(screen, "tomato"));
  await closeFilters(screen, "backdrop");
  await openFilters(screen);
  expect(queryDialogButton(screen, "Remove tomato")).toBeNull();
  expect(dialogButton(screen, "Remove basil")).toBeTruthy();
  expect(mockPushRoute).not.toHaveBeenCalled();
});

test("Clear affects only the draft until Apply", async () => {
  mockRouteParams = { ingredient: "basil", maxTotalMinutes: "30" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  expect(screen.getByRole("button", { name: "Filters (2)" })).toBeTruthy();
  await openFilters(screen);
  await waitForOption(screen, "Remove basil");
  await fireEvent.press(dialogButton(screen, "Clear"));
  expect(queryDialogButton(screen, "Remove basil")).toBeNull();
  expect(mockPushRoute).not.toHaveBeenCalled();
  expect(filtersTrigger(screen).props.accessibilityLabel).toBe("Filters (2)");
  await fireEvent.press(dialogButton(screen, "Apply"));
  expect(mockPushRoute).toHaveBeenCalledTimes(1);
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: {} });
});

test("sort selection pushes immediately and closes its menu", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Sort" })).toBeTruthy());
  expect(screen.queryByPlaceholderText("Sort order")).toBeNull();
  expect(screen.queryByDisplayValue("updatedAt:desc")).toBeNull();
  expect(screen.queryByRole("button", { name: "Best ingredient match" })).toBeNull();
  await openSort(screen);
  expect(screen.getByTestId("sort-menu", hidden)).toBeTruthy();
  await fireEvent.press(dialogButton(screen, "Highest rated"));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { sort: ["rating:desc"] } });
  expect(screen.queryByTestId("sort-menu")).toBeNull();
});

test("defaults layoutMode to medium so Filters is a bounded dialog", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await openFilters(screen);
  expect(StyleSheet.flatten(screen.getByTestId("filter-dialog-panel", hidden).props.style).maxWidth).toBe(720);
});

test("compact layoutMode fills the Filters dialog", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), {}, "compact");
  await openFilters(screen);
  const compactStyle = StyleSheet.flatten(screen.getByTestId("filter-dialog-panel", hidden).props.style);
  expect(compactStyle.flex).toBe(1);
  expect(compactStyle.maxWidth).toBeUndefined();
});

test("restores route-derived values in every visible filter after back-forward navigation", async () => {
  mockRouteParams = { text: "tomato soup", ingredient: ["basil", "tomato"], tag: ["quick", "vegan"], maxTotalMinutes: "30", minRating: "4", ratingState: "rated", sort: ["rating:desc", "totalMinutes:asc"] };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(screen.getByLabelText("Search recipes").props.value).toBe("tomato soup"));
  expect(screen.getByRole("button", { name: "Filters (7)" })).toBeTruthy();
  await openFilters(screen);
  expect(dialogButton(screen, "Remove basil")).toBeTruthy();
  expect(dialogButton(screen, "Rated only")).toBeTruthy();
  await closeFilters(screen, "cancel");
  await openSort(screen);
  expect(dialogButton(screen, "Highest rated").props.accessibilityState).toMatchObject({ selected: true });
  await fireEvent.press(screen.getByTestId("sort-menu-backdrop", hidden));

  mockRouteParams = { text: "risotto", ratingState: "unrated" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await waitFor(() => expect(screen.getByLabelText("Search recipes").props.value).toBe("risotto"));
  expect(screen.getByRole("button", { name: "Filters (1)" })).toBeTruthy();
  await openFilters(screen);
  expect(queryDialogButton(screen, "Remove basil")).toBeNull();
  expect(dialogButton(screen, "Unrated only")).toBeTruthy();
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(2));
});

test("ignores duplicate load-more presses before a rerender and preserves the first page", async () => {
  const next = deferred<Page>();
  const listRecipes = jest.fn().mockResolvedValueOnce({ items: [recipe], nextCursor: "cursor-2" }).mockReturnValueOnce(next.promise);
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(screen.getByRole("button", { name: "Load more recipes" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Load more recipes" }));
  await fireEvent.press(screen.getByRole("button", { name: "Load more recipes" }));
  expect(listRecipes).toHaveBeenCalledTimes(2);
  await act(async () => next.resolve({ items: [recipe, secondRecipe], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("2 recipes loaded")).toBeTruthy());
});

test("keeps a newer pagination guard active when a stale request finishes", () => {
  const guard = createPaginationRequestGuard();
  guard.start(2);
  guard.reset();
  guard.start(4);
  guard.finish(2);
  expect(guard.isActive()).toBe(true);
  guard.finish(4);
  expect(guard.isActive()).toBe(false);
});

test("does not let a stale pagination finally clear a newer pagination guard", async () => {
  const oldPage = deferred<Page>(); const newInitial = deferred<Page>(); const newPage = deferred<Page>();
  const listRecipes = jest.fn()
    .mockResolvedValueOnce({ items: [recipe], nextCursor: "cursor-1" })
    .mockReturnValueOnce(oldPage.promise)
    .mockReturnValueOnce(newInitial.promise)
    .mockReturnValueOnce(newPage.promise);
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(screen.getByRole("button", { name: "Load more recipes" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Load more recipes" }));
  mockRouteParams = { text: "risotto" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await act(async () => newInitial.resolve({ items: [secondRecipe], nextCursor: "cursor-2" }));
  await waitFor(() => expect(screen.getByText("Tomato risotto")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Load more recipes" }));
  await act(async () => oldPage.resolve({ items: [recipe], nextCursor: "stale" }));
  await fireEvent.press(screen.getByRole("button", { name: "Load more recipes" }));
  expect(listRecipes).toHaveBeenCalledTimes(4);
  await act(async () => newPage.resolve({ items: [secondRecipe], nextCursor: null }));
});

test("suppresses out-of-order pagination and search results", async () => {
  const pageTwo = deferred<Page>(); const search = deferred<Page>();
  const listRecipes = jest.fn().mockResolvedValueOnce({ items: [recipe], nextCursor: "cursor-2" }).mockReturnValueOnce(pageTwo.promise).mockReturnValueOnce(search.promise);
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(screen.getByRole("button", { name: "Load more recipes" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Load more recipes" }));
  mockRouteParams = { text: "risotto" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await act(async () => search.resolve({ items: [secondRecipe], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("Tomato risotto")).toBeTruthy());
  await act(async () => pageTwo.resolve({ items: [recipe], nextCursor: "stale-cursor" }));
  expect(screen.queryByText("Lemon pasta")).toBeNull();
  expect(screen.queryByRole("button", { name: "Load more recipes" })).toBeNull();
});

test("suppresses an unauthorized retry response after newer navigation", async () => {
  const initial = deferred<Page>(); const retry = deferred<Page>(); const newer = deferred<Page>();
  const listRecipes = jest.fn().mockReturnValueOnce(initial.promise).mockReturnValueOnce(retry.promise).mockReturnValueOnce(newer.promise);
  const screen = await renderScreen(listRecipes);
  await act(async () => initial.reject(new Error("generic failure")));
  await waitFor(() => expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  mockRouteParams = { text: "risotto" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await act(async () => newer.resolve({ items: [secondRecipe], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("Tomato risotto")).toBeTruthy());
  await act(async () => retry.reject(new ApiUnauthorizedError()));
  expect(actions.onUnauthorized).not.toHaveBeenCalled();
});

test("suppresses an offline retry response after newer navigation", async () => {
  const initial = deferred<Page>(); const retry = deferred<Page>(); const newer = deferred<Page>();
  const listRecipes = jest.fn().mockReturnValueOnce(initial.promise).mockReturnValueOnce(retry.promise).mockReturnValueOnce(newer.promise);
  const screen = await renderScreen(listRecipes);
  await act(async () => initial.reject(new Error("generic failure")));
  await waitFor(() => expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  mockRouteParams = { text: "risotto" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await act(async () => newer.resolve({ items: [secondRecipe], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("Tomato risotto")).toBeTruthy());
  await act(async () => retry.reject(new ApiNetworkError({ code: "ERR_NETWORK" })));
  expect(screen.queryByText("Youâ€™re offline. Check your connection and try again.")).toBeNull();
});

test("does not update or surface errors after an in-flight library request unmounts", async () => {
  const stale = deferred<Page>(); const screen = await renderScreen(jest.fn().mockReturnValueOnce(stale.promise));
  await screen.unmount();
  await act(async () => stale.resolve({ items: [recipe], nextCursor: null }));
  expect(actions.onUnauthorized).not.toHaveBeenCalled();
});

test("shows observed ingredient options with a human label inside Filters", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await openFilters(screen);
  await waitForOption(screen, "tomato");
  expect(screen.getByText("Ingredients", hidden)).toBeTruthy();
  expect(screen.getByLabelText("Ingredients", hidden)).toBeTruthy();
  expect(screen.getByText("Tags", hidden)).toBeTruthy();
  expect(screen.queryByText("requiredIngredient")).toBeNull();
  expect(screen.queryByText("Required ingredients")).toBeNull();
  expect(screen.queryByText("Available ingredients")).toBeNull();
});

test("selecting an observed option updates draft chips immediately and free text does not add", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await openFilters(screen);
  await waitForOption(screen, "tomato");
  await fireEvent.press(dialogButton(screen, "tomato"));
  expect(dialogButton(screen, "Remove tomato")).toBeTruthy();
  expect(mockPushRoute).not.toHaveBeenCalled();
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "ketchup");
  expect(mockPushRoute).not.toHaveBeenCalledWith(expect.objectContaining({ params: expect.objectContaining({ ingredient: expect.arrayContaining(["ketchup"]) }) }));
  expect(queryDialogButton(screen, "Remove ketchup")).toBeNull();
});

test("treats a comma-containing ingredient URL value as one chip", async () => {
  mockRouteParams = { ingredient: "salt, divided" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await openFilters(screen);
  await waitFor(() => expect(screen.getByText("salt, divided", hidden)).toBeTruthy());
  expect(dialogButton(screen, "Remove salt, divided")).toBeTruthy();
});

test("does not add a 33rd ingredient or 17th tag to the draft", async () => {
  mockRouteParams = {
    ingredient: Array.from({ length: 32 }, (_, index) => `ing-${index}`),
    tag: Array.from({ length: 16 }, (_, index) => `tag-${index}`),
  };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await openFilters(screen);
  await waitForOption(screen, "tomato");
  mockPushRoute.mockClear();
  await fireEvent.press(dialogButton(screen, "tomato"));
  await fireEvent.press(dialogButton(screen, "family"));
  expect(dialogButton(screen, "tomato").props.accessibilityState?.disabled).toBe(true);
  expect(dialogButton(screen, "family").props.accessibilityState?.disabled).toBe(true);
  expect(dialogButton(screen, "tomato").props.focusable).toBe(false);
  expect(dialogButton(screen, "family").props.focusable).toBe(false);
  expect(queryDialogButton(screen, "Remove tomato")).toBeNull();
  expect(queryDialogButton(screen, "Remove family")).toBeNull();
  expect(mockPushRoute).not.toHaveBeenCalled();
});

test("hides unavailable labels while a same-selection refresh is in flight or failed", async () => {
  jest.useFakeTimers();
  const secondResolve = deferred<RecipeFacetSelectionsResponse>();
  const resolveRecipeFacetSelections = jest.fn()
    .mockResolvedValueOnce({
      ingredients: [{ requestedName: "ghost pepper", resolvedName: null, status: "unavailable" }],
      tags: [],
    })
    .mockReturnValueOnce(secondResolve.promise);
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage())
    .mockRejectedValueOnce(new Error("search failed"))
    .mockResolvedValue(defaultFacetPage());
  mockRouteParams = { ingredient: "ghost pepper" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), {
    listRecipeFacets,
    resolveRecipeFacetSelections,
  });
  await act(async () => { await Promise.resolve(); });
  await openFilters(screen);
  await waitFor(() => expect(screen.getByText("unavailable", hidden)).toBeTruthy());
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "zuc");
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(dialogButton(screen, "Try filters again")).toBeTruthy());
  await fireEvent.press(dialogButton(screen, "Try filters again"));
  expect(screen.queryByText("unavailable", hidden)).toBeNull();
  await act(async () => { secondResolve.reject(new Error("resolve failed")); });
  expect(screen.queryByText("unavailable", hidden)).toBeNull();
  expect(screen.getByText("ghost pepper", hidden)).toBeTruthy();
});

test("shows an unavailable chip only after observed false", async () => {
  const pending = deferred<RecipeFacetSelectionsResponse>();
  const resolveRecipeFacetSelections = jest.fn().mockReturnValueOnce(pending.promise);
  mockRouteParams = { ingredient: "ghost pepper" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections });
  await openFilters(screen);
  await waitFor(() => expect(screen.getByText("ghost pepper", hidden)).toBeTruthy());
  expect(screen.queryByText("unavailable", hidden)).toBeNull();
  await act(async () => pending.resolve({
    ingredients: [{ requestedName: "ghost pepper", resolvedName: null, status: "unavailable" }],
    tags: [],
  }));
  await waitFor(() => expect(screen.getByText("unavailable", hidden)).toBeTruthy());
  expect(screen.getByText("ghost pepper", hidden)).toBeTruthy();
});

test("rewrites canonical ingredient names with replace instead of push", async () => {
  const resolveRecipeFacetSelections = jest.fn()
    .mockResolvedValueOnce({
      ingredients: [{ requestedName: "Straße", resolvedName: "strasse", status: "observed" }],
      tags: [],
    })
    .mockResolvedValue({
      ingredients: [{ requestedName: "strasse", resolvedName: "strasse", status: "observed" }],
      tags: [],
    });
  mockRouteParams = { ingredient: "Straße" };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes, { resolveRecipeFacetSelections });
  await waitFor(() => expect(mockReplaceRoute).toHaveBeenCalledTimes(1));
  expect(mockReplaceRoute).toHaveBeenCalledWith({ pathname: "/recipes", params: { ingredient: ["strasse"] } });
  expect(mockPushRoute).not.toHaveBeenCalled();
  mockReplaceRoute.mockClear();
  mockRouteParams = { ingredient: "strasse" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes, { resolveRecipeFacetSelections })} {...actions} />);
  await waitFor(() => expect(resolveRecipeFacetSelections).toHaveBeenCalledTimes(2));
  expect(mockReplaceRoute).not.toHaveBeenCalled();
  expect(mockPushRoute).not.toHaveBeenCalled();
});

test("resolves selections after back-forward even when focus callback identity is unchanged", async () => {
  const resolveRecipeFacetSelections = jest.fn().mockImplementation(async (body: { ingredients?: string[] }) => echoResolve(body));
  mockRouteParams = { ingredient: "basil" };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const catalog = catalogWith(listRecipes, { resolveRecipeFacetSelections });
  const screen = await render(<RecipeListScreen catalog={catalog} {...actions} />);
  await waitFor(() => expect(resolveRecipeFacetSelections).toHaveBeenCalledTimes(1));
  mockRouteParams = { ingredient: "tomato" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  await waitFor(() => expect(resolveRecipeFacetSelections).toHaveBeenCalledTimes(2));
  expect(resolveRecipeFacetSelections.mock.calls[1][0]).toEqual(expect.objectContaining({ ingredients: expect.arrayContaining(["tomato"]) }));
});

test("does not let a slow Filters browse overwrite a newer picker search", async () => {
  jest.useFakeTimers();
  const browsePage = deferred<RecipeFacetPage>();
  const listRecipeFacets = jest.fn()
    .mockReturnValueOnce(browsePage.promise)
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["zucchini"] }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "zuc");
  expect(screen.getByLabelText("Ingredients", hidden).props.value).toBe("zuc");
  await act(async () => { browsePage.resolve(defaultFacetPage()); });
  expect(screen.getByLabelText("Ingredients", hidden).props.value).toBe("zuc");
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  expect(listRecipeFacets.mock.calls[1][0]).toEqual(expect.objectContaining({ ingredientQ: "zuc" }));
  expect(listRecipeFacets.mock.calls[1][0]).not.toHaveProperty("ingredientCursor");
  await waitFor(() => expect(dialogButton(screen, "zucchini")).toBeTruthy());
});

test("does not let a late Filters browse overwrite a cleared picker search", async () => {
  jest.useFakeTimers();
  const browsePage = deferred<RecipeFacetPage>();
  const listRecipeFacets = jest.fn()
    .mockReturnValueOnce(browsePage.promise)
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["garlic"] }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "zuc");
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "");
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  expect(listRecipeFacets.mock.calls[1][0]).not.toHaveProperty("ingredientQ");
  await waitFor(() => expect(dialogButton(screen, "garlic")).toBeTruthy());
  await act(async () => { browsePage.resolve(defaultFacetPage()); });
  expect(screen.getByLabelText("Ingredients", hidden).props.value).toBe("");
  expect(dialogButton(screen, "garlic")).toBeTruthy();
});

test("does not let a late Filters browse failure erase a newer picker search", async () => {
  jest.useFakeTimers();
  const browsePage = deferred<RecipeFacetPage>();
  const listRecipeFacets = jest.fn()
    .mockReturnValueOnce(browsePage.promise)
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["zucchini"] }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "zuc");
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(dialogButton(screen, "zucchini")).toBeTruthy());
  await act(async () => { browsePage.reject(new Error("browse failed")); });
  expect(screen.getByLabelText("Ingredients", hidden).props.value).toBe("zuc");
  expect(dialogButton(screen, "zucchini")).toBeTruthy();
  expect(screen.getByText("We couldn't load filter options. Please try again.", hidden)).toBeTruthy();
});

test("keeps unedited tag lane and duration when ingredient search starts during Filters browse", async () => {
  jest.useFakeTimers();
  const browsePage = deferred<RecipeFacetPage>();
  const listRecipeFacets = jest.fn()
    .mockReturnValueOnce(browsePage.promise)
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["zucchini"] }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "zuc");
  await act(async () => { browsePage.resolve(defaultFacetPage()); });
  expect(screen.getByLabelText("Ingredients", hidden).props.value).toBe("zuc");
  expect(screen.queryByLabelText("Loading Tags", hidden)).toBeNull();
  expect(dialogButton(screen, "family")).toBeTruthy();
  expect(dialogButton(screen, "90 minutes")).toBeTruthy();
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(dialogButton(screen, "zucchini")).toBeTruthy());
});

test("debounces independent ingredient and tag searches without clobbering the other lane", async () => {
  jest.useFakeTimers();
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage())
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["zucchini"] }))
    .mockResolvedValueOnce(defaultFacetPage({ tags: ["dinner"] }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "zuc");
  await act(async () => { jest.advanceTimersByTime(499); });
  expect(listRecipeFacets.mock.calls.some((call) => call[0]?.ingredientQ === "zuc")).toBe(false);
  await act(async () => { jest.advanceTimersByTime(1); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  const searchParams = listRecipeFacets.mock.calls[1][0];
  expect(searchParams).toEqual(expect.objectContaining({ ingredientQ: "zuc" }));
  expect(searchParams).not.toHaveProperty("ingredientCursor");
  expect(searchParams).not.toHaveProperty("tagQ");
  await waitFor(() => expect(dialogButton(screen, "zucchini")).toBeTruthy());
  expect(dialogButton(screen, "family")).toBeTruthy();
  await fireEvent.changeText(screen.getByLabelText("Tags", hidden), "din");
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(dialogButton(screen, "dinner")).toBeTruthy());
  expect(dialogButton(screen, "zucchini")).toBeTruthy();
});

test("does not paint a stale lane search after the user types again", async () => {
  jest.useFakeTimers();
  const firstSearch = deferred<RecipeFacetPage>();
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage())
    .mockReturnValueOnce(firstSearch.promise)
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["ketchup"] }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "tom");
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "toma");
  await act(async () => { firstSearch.resolve(defaultFacetPage({ ingredients: ["stale-match"] })); });
  expect(queryDialogButton(screen, "stale-match")).toBeNull();
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(dialogButton(screen, "ketchup")).toBeTruthy());
  expect(queryDialogButton(screen, "stale-match")).toBeNull();
});

test("load more merges unique first-seen names and then disappears", async () => {
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["basil"], ingredientNextCursor: "cursor-1" }))
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["tomato"], ingredientNextCursor: null }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await waitFor(() => expect(dialogButton(screen, "Load more options")).toBeTruthy());
  await fireEvent.press(dialogButton(screen, "Load more options"));
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  expect(listRecipeFacets.mock.calls[1][0]).toEqual(expect.objectContaining({ ingredientCursor: "cursor-1" }));
  await waitFor(() => expect(dialogButton(screen, "tomato")).toBeTruthy());
  expect(dialogButton(screen, "basil")).toBeTruthy();
  await waitFor(() => expect(queryDialogButton(screen, "Load more options")).toBeNull());
});

test("does not join an in-flight ingredient search with the previous page cursor", async () => {
  jest.useFakeTimers();
  const searchPage = deferred<RecipeFacetPage>();
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["basil"], ingredientNextCursor: "cursor-1" }))
    .mockReturnValueOnce(searchPage.promise)
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["oregano"], ingredientNextCursor: null }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(dialogButton(screen, "Load more options")).toBeTruthy());
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "zuc");
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  const loadMoreDuringSearch = queryDialogButton(screen, "Load more options");
  if (loadMoreDuringSearch) {
    await fireEvent.press(loadMoreDuringSearch);
  }
  expect(listRecipeFacets.mock.calls.some((call) => call[0]?.ingredientQ === "zuc" && call[0]?.ingredientCursor)).toBe(false);
  await act(async () => { searchPage.resolve(defaultFacetPage({ ingredients: ["zucchini"], ingredientNextCursor: null })); });
  await waitFor(() => expect(dialogButton(screen, "zucchini")).toBeTruthy());
  expect(queryDialogButton(screen, "oregano")).toBeNull();
  expect(queryDialogButton(screen, "basil")).toBeNull();
});

test("does not join a pending ingredient search with the previous page cursor", async () => {
  jest.useFakeTimers();
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["basil"], ingredientNextCursor: "cursor-1" }))
    .mockResolvedValue(defaultFacetPage({ ingredients: ["zucchini"], ingredientNextCursor: null }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(dialogButton(screen, "Load more options")).toBeTruthy());
  await fireEvent.changeText(screen.getByLabelText("Ingredients", hidden), "zuc");
  expect(queryDialogButton(screen, "Load more options")).toBeNull();
  expect(listRecipeFacets.mock.calls.some((call) => call[0]?.ingredientQ && call[0]?.ingredientCursor)).toBe(false);
  await act(async () => { jest.advanceTimersByTime(500); });
  await waitFor(() => expect(listRecipeFacets.mock.calls.some((call) => call[0]?.ingredientQ === "zuc")).toBe(true));
  const searchCall = listRecipeFacets.mock.calls.find((call) => call[0]?.ingredientQ === "zuc");
  expect(searchCall?.[0]).not.toHaveProperty("ingredientCursor");
});

test("stale facet cursor 409 clears only that lane and restarts page one", async () => {
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["basil"], ingredientNextCursor: "cursor-1" }))
    .mockRejectedValueOnce(new ApiError("stale", 409))
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["garlic"], ingredientNextCursor: null }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await openFilters(screen);
  await waitFor(() => expect(dialogButton(screen, "Load more options")).toBeTruthy());
  await fireEvent.press(dialogButton(screen, "Load more options"));
  await waitFor(() => expect(dialogButton(screen, "garlic")).toBeTruthy());
  expect(queryDialogButton(screen, "basil")).toBeNull();
  expect(listRecipeFacets).toHaveBeenCalledTimes(3);
  expect(listRecipeFacets.mock.calls[2][0]).not.toHaveProperty("ingredientCursor");
});

test("any duration and no minimum omit params and unrated clears min rating", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const catalog = catalogWith(listRecipes);
  const screen = await render(<RecipeListScreen catalog={catalog} {...actions} />);
  await openFilters(screen);
  await waitFor(() => expect(dialogButton(screen, "90 minutes")).toBeTruthy());
  await fireEvent.press(dialogButton(screen, "90 minutes"));
  expect(mockPushRoute).not.toHaveBeenCalled();
  await fireEvent.press(dialogButton(screen, "Apply"));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { maxTotalMinutes: "90" } });
  mockRouteParams = { maxTotalMinutes: "90" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  await openFilters(screen);
  await fireEvent.press(dialogButton(screen, "Any duration"));
  await fireEvent.press(dialogButton(screen, "Apply"));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: {} });
  mockRouteParams = {};
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  await openFilters(screen);
  await fireEvent.press(dialogButton(screen, "4 and up"));
  await fireEvent.press(dialogButton(screen, "Apply"));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { minRating: "4" } });
  mockRouteParams = { minRating: "4" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  await openFilters(screen);
  await fireEvent.press(dialogButton(screen, "No minimum"));
  await fireEvent.press(dialogButton(screen, "Apply"));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: {} });
  mockRouteParams = { minRating: "4" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  await openFilters(screen);
  await fireEvent.press(dialogButton(screen, "Unrated only"));
  await fireEvent.press(dialogButton(screen, "Apply"));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { ratingState: "unrated" } });
});

test("time control keeps out-of-range bookmarks until edited and supports max zero", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  mockRouteParams = { maxTotalMinutes: "999" };
  const screen = await renderScreen(listRecipes);
  await openFilters(screen);
  await waitFor(() => expect(screen.getByText("outside current range", hidden)).toBeTruthy());
  expect(mockPushRoute).not.toHaveBeenCalled();
  await fireEvent.press(dialogButton(screen, "Decrease duration"));
  expect(mockPushRoute).not.toHaveBeenCalled();
  await fireEvent.press(dialogButton(screen, "Apply"));
  expect(mockPushRoute).toHaveBeenCalled();
  const clamped = Number((mockPushRoute.mock.calls.at(-1)?.[0] as { params: { maxTotalMinutes: string } }).params.maxTotalMinutes);
  expect(clamped).toBeGreaterThanOrEqual(0);
  expect(clamped).toBeLessThanOrEqual(90);
  mockPushRoute.mockClear();
  mockRouteParams = { maxTotalMinutes: "45" };
  const unavailable = await renderScreen(listRecipes, { listRecipeFacets: jest.fn().mockResolvedValue(defaultFacetPage({ totalMinutes: null })) });
  await openFilters(unavailable);
  await waitFor(() => expect(unavailable.getByText("unavailable", hidden)).toBeTruthy());
  await fireEvent.press(dialogButton(unavailable, "Clear 45 minutes"));
  expect(mockPushRoute).not.toHaveBeenCalled();
  await fireEvent.press(dialogButton(unavailable, "Apply"));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: {} });
  mockRouteParams = {};
  const zeroMax = await renderScreen(listRecipes, { listRecipeFacets: jest.fn().mockResolvedValue(defaultFacetPage({ totalMinutes: { min: 0, max: 0 } })) });
  await openFilters(zeroMax);
  await waitFor(() => expect(dialogButton(zeroMax, "0 minutes")).toBeTruthy());
  await fireEvent.press(dialogButton(zeroMax, "0 minutes"));
  await fireEvent.press(dialogButton(zeroMax, "Apply"));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { maxTotalMinutes: "0" } });
});

test("clears a selection error after a later automatic resolve succeeds", async () => {
  const resolveRecipeFacetSelections = jest.fn()
    .mockRejectedValueOnce(new Error("resolve failed"))
    .mockResolvedValue(echoResolve({ ingredients: ["tomato"] }));
  mockRouteParams = { ingredient: "basil" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null }), {
    resolveRecipeFacetSelections,
  });
  await openFilters(screen);
  await waitFor(() => expect(screen.getByText("We couldn't load filter options. Please try again.", hidden)).toBeTruthy());
  mockRouteParams = { ingredient: "tomato" };
  await screen.rerender(
    <RecipeListScreen
      catalog={catalogWith(jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null }), { resolveRecipeFacetSelections })}
      {...actions}
    />,
  );
  await waitFor(() => expect(screen.queryByText("We couldn't load filter options. Please try again.", hidden)).toBeNull());
});

test("facet errors remain inside Filters while loaded recipes remain visible", async () => {
  const listRecipeFacets = jest.fn().mockRejectedValue(new Error("https://provider.example/facets access_token=secret"));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null }), { listRecipeFacets });
  await waitFor(() => expect(screen.getByText("Lemon pasta")).toBeTruthy());
  expect(screen.queryByText("We couldn't load filter options. Please try again.")).toBeNull();
  await openFilters(screen);
  await waitFor(() => expect(screen.getByText("We couldn't load filter options. Please try again.", hidden)).toBeTruthy());
  expect(screen.queryByText("https://provider.example/facets access_token=secret")).toBeNull();
  expect(screen.getByTestId("inline-notice", hidden)).toBeTruthy();
  expect(screen.getByText("Lemon pasta")).toBeTruthy();
});

test("facet 401 triggers unauthorized and list 414 stays a generic retry", async () => {
  const unauthorizedFacets = jest.fn().mockRejectedValue(new ApiUnauthorizedError());
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null }), { listRecipeFacets: unauthorizedFacets });
  await openFilters(screen);
  await waitFor(() => expect(actions.onUnauthorized).toHaveBeenCalledTimes(1));
  actions.onUnauthorized.mockClear();
  const failedList = await renderScreen(jest.fn().mockRejectedValue(new ApiError("URI too long", 414)));
  await waitFor(() => expect(failedList.getByText("We couldn't load your recipes. Please try again.")).toBeTruthy());
  expect(actions.onUnauthorized).not.toHaveBeenCalled();
});

test("does not restart library or facet requests when onUnauthorized identity changes", async () => {
  const listRecipeFacets = jest.fn().mockResolvedValue(defaultFacetPage());
  const listRecipes = jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null });
  const catalog = catalogWith(listRecipes, { listRecipeFacets });
  const screen = await render(<RecipeListScreen catalog={catalog} {...actions} />);
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(1));
  expect(listRecipeFacets).toHaveBeenCalledTimes(0);
  await openFilters(screen);
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} onUnauthorized={() => undefined} />);
  expect(listRecipes).toHaveBeenCalledTimes(1);
  expect(listRecipeFacets).toHaveBeenCalledTimes(1);
});

test("loads facets when Filters opens including later reopens and not on Library focus", async () => {
  const listRecipeFacets = jest.fn().mockResolvedValue(defaultFacetPage());
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const catalog = catalogWith(listRecipes, { listRecipeFacets });
  const screen = await render(<RecipeListScreen catalog={catalog} {...actions} />);
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(1));
  expect(listRecipeFacets).toHaveBeenCalledTimes(0);
  await openFilters(screen);
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  await closeFilters(screen, "cancel");
  mockRouteParams = { minRating: "4" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  expect(listRecipeFacets).toHaveBeenCalledTimes(1);
  await openFilters(screen);
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
});

test("ignores a stale selection resolve after back-forward navigation", async () => {
  const pending = deferred<RecipeFacetSelectionsResponse>();
  const resolveRecipeFacetSelections = jest.fn()
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValue({ ingredients: [{ requestedName: "onion", resolvedName: "onion", status: "observed" }], tags: [] });
  mockRouteParams = { ingredient: "Straße" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections });
  mockRouteParams = { ingredient: "onion" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections })} {...actions} />);
  await act(async () => pending.resolve({
    ingredients: [{ requestedName: "Straße", resolvedName: "strasse", status: "observed" }],
    tags: [],
  }));
  expect(mockReplaceRoute).not.toHaveBeenCalledWith(expect.objectContaining({ params: expect.objectContaining({ ingredient: ["strasse"] }) }));
  await openFilters(screen);
  expect(screen.getByText("onion", hidden)).toBeTruthy();
  expect(screen.queryByText("strasse", hidden)).toBeNull();
});

test("applies canonical names to the latest params after an unrelated rating edit", async () => {
  const pending = deferred<RecipeFacetSelectionsResponse>();
  const resolveRecipeFacetSelections = jest.fn().mockReturnValueOnce(pending.promise);
  mockRouteParams = { ingredient: "Straße" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections });
  await openFilters(screen);
  await fireEvent.press(dialogButton(screen, "4 and up"));
  await fireEvent.press(dialogButton(screen, "Apply"));
  expect(mockPushRoute).toHaveBeenCalled();
  mockRouteParams = { ingredient: "Straße", minRating: "4" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections })} {...actions} />);
  await act(async () => pending.resolve({
    ingredients: [{ requestedName: "Straße", resolvedName: "strasse", status: "observed" }],
    tags: [],
  }));
  expect(mockReplaceRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { ingredient: ["strasse"], minRating: "4" } });
});

test("requests recipes with ingredient and tag params from the URL", async () => {
  mockRouteParams = { ingredient: ["basil", "tomato"], tag: "weeknight" };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  await renderScreen(listRecipes);
  await waitFor(() => expect(listRecipes).toHaveBeenCalled());
  expect(listRecipes.mock.calls[0][0]).toEqual(expect.objectContaining({
    ingredient: ["basil", "tomato"],
    tag: ["weeknight"],
  }));
  expect(listRecipes.mock.calls[0][0]).not.toHaveProperty("requiredIngredient");
  expect(listRecipes.mock.calls[0][0]).not.toHaveProperty("availableIngredient");
});
