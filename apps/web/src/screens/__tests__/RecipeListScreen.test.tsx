import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

import { ApiNetworkError, ApiUnauthorizedError } from "../../api/client";
import type { RecipeQueryItem } from "../../api/catalog";
import { createPaginationRequestGuard, isOfflineError, RecipeListScreen } from "../RecipeListScreen";

let mockRouteParams: Record<string, string | string[] | undefined> = {};
const mockPushRoute = jest.fn();
const mockReplaceRoute = jest.fn();
const mockRouter = { push: mockPushRoute, replace: mockReplaceRoute };
jest.mock("expo-router", () => ({
  useLocalSearchParams: () => mockRouteParams,
  useRouter: () => mockRouter,
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

const recipe: RecipeQueryItem = { recipe: { id: "recipe-1", title: "Lemon pasta", sourceUrl: null, servings: 4, prepMinutes: 10, cookMinutes: 15, totalMinutes: 25, ingredients: [], instructions: [], tags: ["pasta"], rating: 4 }, match: null };
const secondRecipe: RecipeQueryItem = { ...recipe, recipe: { ...recipe.recipe, id: "recipe-2", title: "Tomato risotto" } };
type Page = { items: RecipeQueryItem[]; nextCursor: string | null };
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((next, fail) => { resolve = next; reject = fail; }); return { promise, resolve, reject }; }
function catalogWith(listRecipes: jest.Mock) { return { listRecipes } as unknown as React.ComponentProps<typeof RecipeListScreen>["catalog"]; }
const actions = { onOpenDetail: jest.fn(), onCreate: jest.fn(), onImport: jest.fn(), onLogout: jest.fn(), onUnauthorized: jest.fn() };
const renderScreen = (listRecipes: jest.Mock) => render(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);

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
  expect(screen.getByLabelText("Required ingredients").props.value).toBe("basil, tomato");
  expect(screen.getByLabelText("Available ingredients").props.value).toBe("onion");
  expect(screen.getByLabelText("Required tags").props.value).toBe("quick, vegan");
  expect(screen.getByLabelText("Preferred tags").props.value).toBe("family");
  expect(screen.getByLabelText("Maximum total minutes").props.value).toBe("30");
  expect(screen.getByLabelText("Minimum rating").props.value).toBe("4");
  expect(screen.getByRole("button", { name: "Highest rated" }).props.accessibilityState).toMatchObject({ selected: true });
  expect(screen.queryByDisplayValue("rating:desc, totalMinutes:asc")).toBeNull();
  expect(screen.queryByDisplayValue("requiredIngredient")).toBeNull();
  expect(screen.getByText("Required ingredients")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Rated only" })).toBeTruthy();

  mockRouteParams = { text: "risotto", ratingState: "unrated" };
  await screen.rerender(<RecipeListScreen catalog={catalogWith(listRecipes)} {...actions} />);
  await waitFor(() => expect(screen.getByLabelText("Search recipes").props.value).toBe("risotto"));
  expect(screen.getByLabelText("Required ingredients").props.value).toBe("");
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

test("commits filters as history entries", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await fireEvent(screen.getByLabelText("Required ingredients"), "endEditing", { nativeEvent: { text: " Tomato, basil " } });
  expect(mockPushRoute).toHaveBeenLastCalledWith({ pathname: "/recipes", params: { requiredIngredient: ["basil", "tomato"] } });
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
