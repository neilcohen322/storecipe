import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

import { ApiError, ApiNetworkError, ApiUnauthorizedError } from "../../api/client";
import type { RecipeFacetPage, RecipeFacetSelectionsResponse, RecipeQueryItem } from "../../api/catalog";
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

const recipe: RecipeQueryItem = { recipe: { id: "recipe-1", title: "Lemon pasta", sourceUrl: null, servings: 4, prepMinutes: 10, cookMinutes: 15, totalMinutes: 25, ingredients: [], instructions: [], tags: ["pasta"], rating: 4 }, match: null };
const secondRecipe: RecipeQueryItem = { ...recipe, recipe: { ...recipe.recipe, id: "recipe-2", title: "Tomato risotto" } };
type Page = { items: RecipeQueryItem[]; nextCursor: string | null };
type CatalogExtras = { listRecipeFacets?: jest.Mock; resolveRecipeFacetSelections?: jest.Mock };
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
    sort: {
      unconditional: ["rating:asc", "rating:desc", "totalMinutes:asc", "totalMinutes:desc", "createdAt:asc", "createdAt:desc", "updatedAt:asc", "updatedAt:desc", "title:asc", "title:desc"],
      requiresAvailableIngredient: ["ingredientCoverage:asc", "ingredientCoverage:desc"],
      requiresPreferredTag: ["tagCoverage:asc", "tagCoverage:desc"],
    },
    ...overrides,
  };
}
function echoResolve(body: { ingredients?: string[]; tags?: string[] } = {}): RecipeFacetSelectionsResponse {
  return {
    ingredients: (body.ingredients ?? []).map((requestedName) => ({ requestedName, normalizedName: requestedName, observed: true })),
    tags: (body.tags ?? []).map((requestedName) => ({ requestedName, normalizedName: requestedName, observed: true })),
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
const renderScreen = (listRecipes: jest.Mock, extras: CatalogExtras = {}) => render(<RecipeListScreen catalog={catalogWith(listRecipes, extras)} {...actions} />);

beforeEach(() => { jest.clearAllMocks(); jest.useRealTimers(); mockRouteParams = {}; });
afterEach(() => { jest.useRealTimers(); });

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

test("debounces search into a history entry instead of replacing history", async () => {
  jest.useFakeTimers();
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await fireEvent.changeText(screen.getByLabelText("Search recipes"), "  Tomato   soup ");
  await act(async () => { jest.advanceTimersByTime(299); });
  expect(mockPushRoute).not.toHaveBeenCalled();
  await act(async () => { jest.advanceTimersByTime(1); });
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { text: "tomato soup" } });
  expect(mockReplaceRoute).not.toHaveBeenCalled();
});

test("updates the search draft immediately and resyncs it from browser navigation", async () => {
  jest.useFakeTimers();
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await fireEvent.changeText(screen.getByLabelText("Search recipes"), "lemon");
  expect(screen.getByLabelText("Search recipes").props.value).toBe("lemon");
  mockRouteParams = { text: "risotto" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await waitFor(() => expect(screen.getByLabelText("Search recipes").props.value).toBe("risotto"));
});

test("does not let an older search debounce overwrite newer navigation", async () => {
  jest.useFakeTimers();
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await fireEvent.changeText(screen.getByLabelText("Search recipes"), "lemon");
  mockRouteParams = { text: "risotto" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await act(async () => { jest.advanceTimersByTime(300); });
  expect(mockPushRoute).not.toHaveBeenCalled();
});

test("restores route-derived values in every visible filter after back-forward navigation", async () => {
  mockRouteParams = { text: "tomato soup", requiredIngredient: ["basil", "tomato"], availableIngredient: "onion", requiredTag: ["quick", "vegan"], preferredTag: "family", maxTotalMinutes: "30", minRating: "4", ratingState: "rated", sort: ["rating:desc", "totalMinutes:asc"] };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(screen.getByLabelText("Search recipes").props.value).toBe("tomato soup"));
  expect(screen.getByRole("button", { name: "Remove basil" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove tomato" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove onion" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove quick" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove vegan" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Remove family" })).toBeTruthy();
  expect(screen.getByLabelText("30 minutes")).toBeTruthy();
  expect(screen.getByRole("button", { name: "4 and up" }).props.accessibilityState).toMatchObject({ selected: true });
  expect(screen.getByRole("button", { name: "Highest rated" }).props.accessibilityState).toMatchObject({ selected: true });
  expect(screen.queryByDisplayValue("rating:desc, totalMinutes:asc")).toBeNull();
  expect(screen.queryByText("requiredIngredient")).toBeNull();
  expect(screen.getByText("Required ingredients")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Rated only" })).toBeTruthy();

  mockRouteParams = { text: "risotto", ratingState: "unrated" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await waitFor(() => expect(screen.getByLabelText("Search recipes").props.value).toBe("risotto"));
  expect(screen.queryByRole("button", { name: "Remove basil" })).toBeNull();
  expect(screen.getByRole("button", { name: "Unrated only" })).toBeTruthy();
  await waitFor(() => expect(listRecipes).toHaveBeenCalledTimes(2));
});

test("sorts with named choices instead of raw query-parameter strings", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Recently updated" })).toBeTruthy());
  expect(screen.queryByPlaceholderText("Sort order")).toBeNull();
  expect(screen.queryByDisplayValue("updatedAt:desc")).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Highest rated" }));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { sort: ["rating:desc"] } });
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
  guard.reset(); // a newer query retires the old pagination scope
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

test("shows observed ingredient options with a human label", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await waitFor(() => expect(screen.getAllByRole("button", { name: "tomato" }).length).toBeGreaterThan(0));
  expect(screen.getByText("Required ingredients")).toBeTruthy();
  expect(screen.getByLabelText("Required ingredients")).toBeTruthy();
  expect(screen.queryByText("requiredIngredient")).toBeNull();
});

test("selecting an observed option pushes history and free text does not add", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await waitFor(() => expect(screen.getAllByRole("button", { name: "tomato" }).length).toBeGreaterThan(0));
  await fireEvent.press(screen.getAllByRole("button", { name: "tomato" })[0]);
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { requiredIngredient: ["tomato"] } });
  await fireEvent.changeText(screen.getByLabelText("Required ingredients"), "ketchup");
  expect(mockPushRoute).not.toHaveBeenCalledWith(expect.objectContaining({ params: expect.objectContaining({ requiredIngredient: expect.arrayContaining(["ketchup"]) }) }));
  expect(screen.queryByRole("button", { name: "Remove ketchup" })).toBeNull();
});

test("treats a comma-containing ingredient URL value as one chip", async () => {
  mockRouteParams = { requiredIngredient: "salt, divided" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("salt, divided")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Remove salt, divided" })).toBeTruthy();
});

test("shows an unavailable chip only after observed false", async () => {
  const pending = deferred<RecipeFacetSelectionsResponse>();
  const resolveRecipeFacetSelections = jest.fn().mockReturnValueOnce(pending.promise);
  mockRouteParams = { requiredIngredient: "ghost pepper" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections });
  await waitFor(() => expect(screen.getByText("ghost pepper")).toBeTruthy());
  expect(screen.queryByText("unavailable")).toBeNull();
  await act(async () => pending.resolve({
    ingredients: [{ requestedName: "ghost pepper", normalizedName: "ghost pepper", observed: false }],
    tags: [],
  }));
  await waitFor(() => expect(screen.getByText("unavailable")).toBeTruthy());
  expect(screen.getByText("ghost pepper")).toBeTruthy();
});

test("rewrites canonical ingredient names with replace instead of push", async () => {
  const resolveRecipeFacetSelections = jest.fn()
    .mockResolvedValueOnce({
      ingredients: [{ requestedName: "Straße", normalizedName: "strasse", observed: true }],
      tags: [],
    })
    .mockResolvedValue({
      ingredients: [{ requestedName: "strasse", normalizedName: "strasse", observed: true }],
      tags: [],
    });
  mockRouteParams = { requiredIngredient: "Straße" };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const screen = await renderScreen(listRecipes, { resolveRecipeFacetSelections });
  await waitFor(() => expect(mockReplaceRoute).toHaveBeenCalledTimes(1));
  expect(mockReplaceRoute).toHaveBeenCalledWith({ pathname: "/recipes", params: { requiredIngredient: ["strasse"] } });
  expect(mockPushRoute).not.toHaveBeenCalled();
  mockReplaceRoute.mockClear();
  mockRouteParams = { requiredIngredient: "strasse" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes, { resolveRecipeFacetSelections })} {...actions} />);
  await waitFor(() => expect(resolveRecipeFacetSelections).toHaveBeenCalledTimes(2));
  expect(mockReplaceRoute).not.toHaveBeenCalled();
  expect(mockPushRoute).not.toHaveBeenCalled();
});

test("resolves selections after back-forward even when focus callback identity is unchanged", async () => {
  const resolveRecipeFacetSelections = jest.fn().mockImplementation(async (body: { ingredients?: string[] }) => echoResolve(body));
  mockRouteParams = { requiredIngredient: "basil" };
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const catalog = catalogWith(listRecipes, { resolveRecipeFacetSelections });
  const screen = await render(<RecipeListScreen catalog={catalog} {...actions} />);
  await waitFor(() => expect(resolveRecipeFacetSelections).toHaveBeenCalledTimes(1));
  mockRouteParams = { requiredIngredient: "tomato" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  await waitFor(() => expect(resolveRecipeFacetSelections).toHaveBeenCalledTimes(2));
  expect(resolveRecipeFacetSelections.mock.calls[1][0]).toEqual(expect.objectContaining({ ingredients: expect.arrayContaining(["tomato"]) }));
});

test("debounces isolated ingredient-lane search without clobbering the other lane", async () => {
  jest.useFakeTimers();
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage())
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["zucchini"] }))
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["basmati"] }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Required ingredients"), "zuc");
  await act(async () => { jest.advanceTimersByTime(299); });
  expect(listRecipeFacets.mock.calls.some((call) => call[0]?.ingredientQ === "zuc")).toBe(false);
  await act(async () => { jest.advanceTimersByTime(1); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  const searchParams = listRecipeFacets.mock.calls[1][0];
  expect(searchParams).toEqual(expect.objectContaining({ ingredientQ: "zuc" }));
  expect(searchParams).not.toHaveProperty("ingredientCursor");
  expect(searchParams).not.toHaveProperty("tagQ");
  await waitFor(() => expect(screen.getByRole("button", { name: "zucchini" })).toBeTruthy());
  expect(screen.getAllByRole("button", { name: "basil" }).length).toBeGreaterThan(0);
  expect(screen.getAllByRole("button", { name: "tomato" }).length).toBeGreaterThan(0);
  await fireEvent.changeText(screen.getByLabelText("Available ingredients"), "bas");
  await act(async () => { jest.advanceTimersByTime(300); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(screen.getByRole("button", { name: "basmati" })).toBeTruthy());
  expect(screen.getByRole("button", { name: "zucchini" })).toBeTruthy();
});

test("load more merges unique first-seen names and then disappears", async () => {
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["basil"], ingredientNextCursor: "cursor-1" }))
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["tomato"], ingredientNextCursor: null }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Load more options" }).length).toBeGreaterThan(0));
  await fireEvent.press(screen.getAllByRole("button", { name: "Load more options" })[0]);
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  expect(listRecipeFacets.mock.calls[1][0]).toEqual(expect.objectContaining({ ingredientCursor: "cursor-1" }));
  await waitFor(() => expect(screen.getAllByRole("button", { name: "tomato" }).length).toBeGreaterThan(0));
  expect(screen.getAllByRole("button", { name: "basil" }).length).toBeGreaterThan(0);
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Load more options" }).length).toBe(1));
});

test("does not join an in-flight required-ingredient search with the previous page cursor", async () => {
  jest.useFakeTimers();
  const searchPage = deferred<RecipeFacetPage>();
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["basil"], ingredientNextCursor: "cursor-1" }))
    .mockReturnValueOnce(searchPage.promise)
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["oregano"], ingredientNextCursor: null }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await act(async () => { await Promise.resolve(); });
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Load more options" }).length).toBe(2));
  await fireEvent.changeText(screen.getByLabelText("Required ingredients"), "zuc");
  await act(async () => { jest.advanceTimersByTime(300); });
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(2));
  const loadMoreDuringSearch = screen.queryAllByRole("button", { name: "Load more options" });
  if (loadMoreDuringSearch.length > 1) {
    await fireEvent.press(loadMoreDuringSearch[0]);
  }
  expect(listRecipeFacets.mock.calls.some((call) => call[0]?.ingredientQ === "zuc" && call[0]?.ingredientCursor)).toBe(false);
  await act(async () => { searchPage.resolve(defaultFacetPage({ ingredients: ["zucchini"], ingredientNextCursor: null })); });
  await waitFor(() => expect(screen.getByRole("button", { name: "zucchini" })).toBeTruthy());
  expect(screen.queryByRole("button", { name: "oregano" })).toBeNull();
  expect(screen.getAllByRole("button", { name: "basil" })).toHaveLength(1);
});

test("stale facet cursor 409 clears only that lane and restarts page one", async () => {
  const listRecipeFacets = jest.fn()
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["basil"], ingredientNextCursor: "cursor-1" }))
    .mockRejectedValueOnce(new ApiError("stale", 409))
    .mockResolvedValueOnce(defaultFacetPage({ ingredients: ["garlic"], ingredientNextCursor: null }));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { listRecipeFacets });
  await waitFor(() => expect(screen.getAllByRole("button", { name: "Load more options" }).length).toBeGreaterThan(0));
  await fireEvent.press(screen.getAllByRole("button", { name: "Load more options" })[0]);
  await waitFor(() => expect(screen.getAllByRole("button", { name: "garlic" }).length).toBeGreaterThan(0));
  expect(screen.getAllByRole("button", { name: "basil" }).length).toBeGreaterThan(0);
  expect(listRecipeFacets).toHaveBeenCalledTimes(3);
  expect(listRecipeFacets.mock.calls[2][0]).not.toHaveProperty("ingredientCursor");
});

test("any duration and no minimum omit params and unrated clears min rating", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const catalog = catalogWith(listRecipes);
  const screen = await render(<RecipeListScreen catalog={catalog} {...actions} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "90 minutes" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "90 minutes" }));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { maxTotalMinutes: "90" } });
  await fireEvent.press(screen.getByRole("button", { name: "Any duration" }));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: {} });
  await fireEvent.press(screen.getByRole("button", { name: "4 and up" }));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { minRating: "4" } });
  await fireEvent.press(screen.getByRole("button", { name: "No minimum" }));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: {} });
  mockRouteParams = { minRating: "4" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  await fireEvent.press(screen.getByRole("button", { name: "Unrated only" }));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { ratingState: "unrated" } });
});

test("coverage sort chips appear only with supporting filters", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const catalog = catalogWith(listRecipes);
  const screen = await render(<RecipeListScreen catalog={catalog} {...actions} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "Recently updated" })).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Best ingredient match" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Best tag match" })).toBeNull();
  mockRouteParams = { availableIngredient: "basil", preferredTag: "family" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  expect(screen.getByRole("button", { name: "Best ingredient match" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Best tag match" })).toBeTruthy();
  mockRouteParams = { availableIngredient: "basil", preferredTag: "family", sort: "ingredientCoverage:desc" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  await fireEvent.press(screen.getByRole("button", { name: "Remove basil" }));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { preferredTag: ["family"] } });
});

test("time control keeps out-of-range bookmarks until edited and supports max zero", async () => {
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  mockRouteParams = { maxTotalMinutes: "999" };
  const screen = await renderScreen(listRecipes);
  await waitFor(() => expect(screen.getByText("outside current range")).toBeTruthy());
  expect(mockPushRoute).not.toHaveBeenCalled();
  await fireEvent.press(screen.getByRole("button", { name: "Decrease duration" }));
  expect(mockPushRoute).toHaveBeenCalled();
  const clamped = Number((mockPushRoute.mock.calls.at(-1)?.[0] as { params: { maxTotalMinutes: string } }).params.maxTotalMinutes);
  expect(clamped).toBeGreaterThanOrEqual(0);
  expect(clamped).toBeLessThanOrEqual(90);
  mockPushRoute.mockClear();
  mockRouteParams = { maxTotalMinutes: "45" };
  const unavailable = await renderScreen(listRecipes, { listRecipeFacets: jest.fn().mockResolvedValue(defaultFacetPage({ totalMinutes: null })) });
  await waitFor(() => expect(unavailable.getByText("unavailable")).toBeTruthy());
  await fireEvent.press(unavailable.getByRole("button", { name: "Clear 45 minutes" }));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: {} });
  mockRouteParams = {};
  const zeroMax = await renderScreen(listRecipes, { listRecipeFacets: jest.fn().mockResolvedValue(defaultFacetPage({ totalMinutes: { min: 0, max: 0 } })) });
  await waitFor(() => expect(zeroMax.getByRole("button", { name: "0 minutes" })).toBeTruthy());
  await fireEvent.press(zeroMax.getByRole("button", { name: "0 minutes" }));
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { maxTotalMinutes: "0" } });
});

test("facet failure shows an inline notice without clearing recipes", async () => {
  const listRecipeFacets = jest.fn().mockRejectedValue(new Error("https://provider.example/facets access_token=secret"));
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null }), { listRecipeFacets });
  await waitFor(() => expect(screen.getByText("Lemon pasta")).toBeTruthy());
  await waitFor(() => expect(screen.getByText("We couldn't load filter options. Please try again.")).toBeTruthy());
  expect(screen.queryByText("https://provider.example/facets access_token=secret")).toBeNull();
  expect(screen.getByTestId("inline-notice")).toBeTruthy();
});

test("facet 401 triggers unauthorized and list 414 stays a generic retry", async () => {
  const unauthorizedFacets = jest.fn().mockRejectedValue(new ApiUnauthorizedError());
  await renderScreen(jest.fn().mockResolvedValue({ items: [recipe], nextCursor: null }), { listRecipeFacets: unauthorizedFacets });
  await waitFor(() => expect(actions.onUnauthorized).toHaveBeenCalledTimes(1));
  actions.onUnauthorized.mockClear();
  const screen = await renderScreen(jest.fn().mockRejectedValue(new ApiError("URI too long", 414)));
  await waitFor(() => expect(screen.getByText("We couldn't load your recipes. Please try again.")).toBeTruthy());
  expect(actions.onUnauthorized).not.toHaveBeenCalled();
});

test("loads facets on focus and does not refetch when only min rating changes", async () => {
  const listRecipeFacets = jest.fn().mockResolvedValue(defaultFacetPage());
  const listRecipes = jest.fn().mockResolvedValue({ items: [], nextCursor: null });
  const catalog = catalogWith(listRecipes, { listRecipeFacets });
  const screen = await render(<RecipeListScreen catalog={catalog} {...actions} />);
  await waitFor(() => expect(listRecipeFacets).toHaveBeenCalledTimes(1));
  mockRouteParams = { minRating: "4" };
  await screen.rerender(<RecipeListScreen catalog={catalog} {...actions} />);
  expect(listRecipeFacets).toHaveBeenCalledTimes(1);
});

test("ignores a stale selection resolve after back-forward navigation", async () => {
  const pending = deferred<RecipeFacetSelectionsResponse>();
  const resolveRecipeFacetSelections = jest.fn()
    .mockReturnValueOnce(pending.promise)
    .mockResolvedValue({ ingredients: [{ requestedName: "onion", normalizedName: "onion", observed: true }], tags: [] });
  mockRouteParams = { requiredIngredient: "Straße" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections });
  mockRouteParams = { requiredIngredient: "onion" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections })} {...actions} />);
  await act(async () => pending.resolve({
    ingredients: [{ requestedName: "Straße", normalizedName: "strasse", observed: true }],
    tags: [],
  }));
  expect(mockReplaceRoute).not.toHaveBeenCalledWith(expect.objectContaining({ params: expect.objectContaining({ requiredIngredient: ["strasse"] }) }));
  expect(screen.getByText("onion")).toBeTruthy();
  expect(screen.queryByText("strasse")).toBeNull();
});

test("applies canonical names to the latest params after an unrelated rating edit", async () => {
  const pending = deferred<RecipeFacetSelectionsResponse>();
  const resolveRecipeFacetSelections = jest.fn().mockReturnValueOnce(pending.promise);
  mockRouteParams = { requiredIngredient: "Straße" };
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections });
  await fireEvent.press(screen.getByRole("button", { name: "4 and up" }));
  expect(mockPushRoute).toHaveBeenCalled();
  mockRouteParams = { requiredIngredient: "Straße", minRating: "4" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(jest.fn().mockResolvedValue({ items: [], nextCursor: null }), { resolveRecipeFacetSelections })} {...actions} />);
  await act(async () => pending.resolve({
    ingredients: [{ requestedName: "Straße", normalizedName: "strasse", observed: true }],
    tags: [],
  }));
  expect(mockReplaceRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { requiredIngredient: ["strasse"], minRating: "4" } });
});

