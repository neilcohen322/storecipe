import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react-native";

import { ApiError, ApiUnauthorizedError } from "../../api/client";
import type { Recipe } from "../../api/catalog";
import { CreateRecipeScreen } from "../CreateRecipeScreen";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 12, left: 0 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: { colors: { canvas: "#fff", surface: "#fff", elevatedSurface: "#fff", text: "#111", mutedText: "#555", border: "#ddd", accent: "#080", accentHover: "#060", accentContrast: "#fff", success: "#080", warning: "#850", danger: "#b00", focusRing: "#080", scrim: "rgba(0,0,0,.4)" }, spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, "2xl": 48 }, sizing: { control: 44, icon: 24, touchTarget: 48 }, radii: { sm: 8, md: 12, lg: 16, pill: 999 }, type: { caption: 12, body: 15, subtitle: 18, heading: 28, display: 54 } } }),
}));

const recipe: Recipe = { id: "recipe-1", title: "Soup", sourceUrl: null, servings: null, prepMinutes: null, cookMinutes: null, totalMinutes: null, ingredients: [], instructions: [], tags: [], rating: null };
const actions = { onCreated: jest.fn(), onBack: jest.fn(), onUnauthorized: jest.fn() };
const deferred = <T,>() => { let resolve!: (value: T) => void; let reject!: (error: unknown) => void; const promise = new Promise<T>((next, fail) => { resolve = next; reject = fail; }); return { promise, resolve, reject }; };

const normalizedIngredients = {
  ingredients: [
    { rawText: "water", name: "water", canonicalName: "water", quantity: null, unit: null },
    { rawText: "salt", name: "salt", canonicalName: "salt", quantity: null, unit: null },
  ],
};

const catalogWith = (createRecipe: jest.Mock) => ({ createRecipe }) as unknown as React.ComponentProps<typeof CreateRecipeScreen>["catalog"];
const ingestionWith = (normalizeIngredients: jest.Mock) => ({ normalizeIngredients }) as unknown as React.ComponentProps<typeof CreateRecipeScreen>["ingestion"];

async function renderScreen(
  createRecipe = jest.fn().mockResolvedValue(recipe),
  normalizeIngredients = jest.fn().mockResolvedValue(normalizedIngredients),
  layoutMode: "compact" | "medium" | "expanded" = "medium",
) {
  return await render(
    <CreateRecipeScreen
      catalog={catalogWith(createRecipe)}
      ingestion={ingestionWith(normalizeIngredients)}
      {...actions}
      layoutMode={layoutMode}
    />,
  );
}

async function fillValidRecipe(screen: Awaited<ReturnType<typeof renderScreen>>, title = "  Soup  ") {
  await fireEvent.changeText(screen.getByLabelText("Title"), title);
  await fireEvent.changeText(screen.getByLabelText("Ingredients"), " water \n\n salt ");
  await fireEvent.changeText(screen.getByLabelText("Instructions"), " boil \n serve ");
}

async function reviewRecipe(screen: Awaited<ReturnType<typeof renderScreen>>) {
  await fireEvent.press(screen.getByRole("button", { name: "Review recipe" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Save recipe" })).toBeTruthy());
}

beforeEach(() => jest.clearAllMocks());
afterEach(cleanup);

test("shows inline required-field errors without losing entered content", async () => {
  const screen = await renderScreen();
  await fireEvent.press(screen.getByRole("button", { name: "Review recipe" }));
  expect(screen.getByText("Title is required.")).toBeTruthy();
  expect(screen.getByText("Add at least one ingredient.")).toBeTruthy();
  expect(screen.getByText("Add at least one instruction.")).toBeTruthy();
  expect(screen.getByLabelText("Title").props.accessibilityState).toMatchObject({ invalid: true });
});

test("reviews from the keyboard once and saves reviewed structured fields", async () => {
  const createRecipe = jest.fn().mockResolvedValue(recipe);
  const normalizeIngredients = jest.fn().mockResolvedValue(normalizedIngredients);
  const screen = await renderScreen(createRecipe, normalizeIngredients);
  await fillValidRecipe(screen);
  await fireEvent(screen.getByLabelText("Instructions"), "submitEditing");
  await waitFor(() => expect(normalizeIngredients).toHaveBeenCalledWith(
    [{ rawText: "water" }, { rawText: "salt" }],
    expect.any(String),
  ));
  await waitFor(() => expect(screen.getByRole("button", { name: "Save recipe" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Save recipe" }));
  await waitFor(() => expect(createRecipe).toHaveBeenCalledWith({
    title: "Soup",
    ingredients: normalizedIngredients.ingredients,
    instructions: ["boil", "serve"],
    tags: [],
  }, expect.any(String)));
  await waitFor(() => expect(actions.onCreated).toHaveBeenCalledWith("recipe-1"));
});

test("prevents duplicate review submissions while a request is in flight", async () => {
  const pendingReview = deferred<typeof normalizedIngredients>();
  const normalizeIngredients = jest.fn(() => pendingReview.promise);
  const screen = await renderScreen(jest.fn(), normalizeIngredients);
  await fillValidRecipe(screen);
  await fireEvent.press(screen.getByRole("button", { name: "Review recipe" }));
  await fireEvent(screen.getByLabelText("Instructions"), "submitEditing");
  await fireEvent.press(screen.getByRole("button", { name: "Review recipe" }));
  expect(normalizeIngredients).toHaveBeenCalledTimes(1);
  await act(async () => { pendingReview.resolve(normalizedIngredients); await pendingReview.promise; });
  await waitFor(() => expect(screen.getByRole("button", { name: "Save recipe" })).toBeTruthy());
});

test("prevents duplicate save submissions while a request is in flight", async () => {
  const pendingSave = deferred<Recipe>();
  const createRecipe = jest.fn(() => pendingSave.promise);
  const screen = await renderScreen(createRecipe, jest.fn().mockResolvedValue(normalizedIngredients));
  await fillValidRecipe(screen);
  await reviewRecipe(screen);
  await fireEvent.press(screen.getByRole("button", { name: "Save recipe" }));
  await fireEvent.press(screen.getByRole("button", { name: "Save recipe" }));
  expect(createRecipe).toHaveBeenCalledTimes(1);
  await act(async () => { pendingSave.resolve(recipe); await pendingSave.promise; });
  await waitFor(() => expect(actions.onCreated).toHaveBeenCalledWith("recipe-1"));
});

test("reuses normalization key for unchanged raw lines and catalog key for unchanged reviewed payload", async () => {
  const normalizeIngredients = jest.fn()
    .mockRejectedValueOnce(new Error("transport details"))
    .mockResolvedValueOnce(normalizedIngredients);
  const createRecipe = jest.fn()
    .mockRejectedValueOnce(new Error("transport details"))
    .mockResolvedValueOnce(recipe);
  const screen = await renderScreen(createRecipe, normalizeIngredients);
  await fillValidRecipe(screen);
  await fireEvent.press(screen.getByRole("button", { name: "Review recipe" }));
  await waitFor(() => expect(screen.getByText("We couldn't review your recipe. Please try again.")).toBeTruthy());
  await fireEvent.changeText(screen.getByLabelText("Ingredients"), "\n water \n\n salt \n");
  await fireEvent.press(screen.getByRole("button", { name: "Review recipe" }));
  await waitFor(() => expect(normalizeIngredients).toHaveBeenCalledTimes(2));
  expect(normalizeIngredients.mock.calls[1]?.[1]).toBe(normalizeIngredients.mock.calls[0]?.[1]);
  await waitFor(() => expect(screen.getByRole("button", { name: "Save recipe" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Save recipe" }));
  await waitFor(() => expect(screen.getByText("We couldn't create your recipe. Please try again.")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Save recipe" }));
  await waitFor(() => expect(createRecipe).toHaveBeenCalledTimes(2));
  expect(createRecipe.mock.calls[1]?.[1]).toBe(createRecipe.mock.calls[0]?.[1]);
});

test("invalidates review when raw ingredient lines change meaningfully", async () => {
  const normalizeIngredients = jest.fn().mockResolvedValue(normalizedIngredients);
  const screen = await renderScreen(jest.fn(), normalizeIngredients);
  await fillValidRecipe(screen);
  await reviewRecipe(screen);
  await fireEvent.changeText(screen.getByLabelText("Ingredients"), "water\npepper");
  expect(screen.getByRole("button", { name: "Review recipe" })).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Save recipe" })).toBeNull();
});

test("rotates catalog key after reviewed title edit without re-normalizing", async () => {
  const normalizeIngredients = jest.fn().mockResolvedValue(normalizedIngredients);
  const createRecipe = jest.fn().mockResolvedValue(recipe);
  const screen = await renderScreen(createRecipe, normalizeIngredients);
  await fillValidRecipe(screen);
  await reviewRecipe(screen);
  expect(normalizeIngredients).toHaveBeenCalledTimes(1);
  await fireEvent.changeText(screen.getByLabelText("Title"), "Stew");
  await fireEvent.press(screen.getByRole("button", { name: "Save recipe" }));
  await waitFor(() => expect(createRecipe).toHaveBeenCalledTimes(1));
  expect(normalizeIngredients).toHaveBeenCalledTimes(1);
  expect(createRecipe.mock.calls[0]?.[0]).toMatchObject({ title: "Stew" });
});

test("uses the compact sticky submit on compact layouts", async () => {
  const compact = await renderScreen(jest.fn(), jest.fn(), "compact");
  expect(compact.getByTestId("create-recipe-sticky-submit")).toBeTruthy();
  expect(compact.queryByTestId("create-recipe-header-submit")).toBeNull();
  expect(compact.getByRole("button", { name: "Review recipe" })).toBeTruthy();
});

test("uses the page-header submit on medium layouts", async () => {
  const medium = await renderScreen(jest.fn(), jest.fn(), "medium");
  expect(medium.getByTestId("create-recipe-header-submit")).toBeTruthy();
  expect(medium.queryByTestId("create-recipe-sticky-submit")).toBeNull();
  expect(medium.getByRole("button", { name: "Review recipe" })).toBeTruthy();
});

test.each([
  [409, "This recipe was already submitted with different content. Change something and try again."],
  [422, "Some fields are invalid. Check your recipe and try again."],
  [429, "Too many requests. Please wait a moment and try again."],
  [503, "The service is temporarily unavailable. Please try again later."],
])("maps save %s responses to safe copy", async (status, message) => {
  const normalizeIngredients = jest.fn().mockResolvedValue(normalizedIngredients);
  const createRecipe = jest.fn().mockRejectedValue(new ApiError("private provider details", status));
  const screen = await renderScreen(createRecipe, normalizeIngredients);
  await fillValidRecipe(screen);
  await reviewRecipe(screen);
  await fireEvent.press(screen.getByRole("button", { name: "Save recipe" }));
  await waitFor(() => expect(screen.getByText(message)).toBeTruthy());
  expect(screen.queryByText("private provider details")).toBeNull();
});

test("retains content and redirects unauthorized responses without exposing details", async () => {
  const screen = await renderScreen(
    jest.fn().mockRejectedValue(new ApiUnauthorizedError("private provider details")),
    jest.fn().mockResolvedValue(normalizedIngredients),
  );
  await fillValidRecipe(screen, "Minestrone");
  await reviewRecipe(screen);
  await fireEvent.press(screen.getByRole("button", { name: "Save recipe" }));
  await waitFor(() => expect(actions.onUnauthorized).toHaveBeenCalledTimes(1));
  expect(screen.getByLabelText("Title").props.value).toBe("Minestrone");
  expect(screen.queryByText("private provider details")).toBeNull();
});

test("maps review failures to safe copy without provider details", async () => {
  const screen = await renderScreen(
    jest.fn(),
    jest.fn().mockRejectedValue(new ApiError("private provider details", 503)),
  );
  await fillValidRecipe(screen);
  await fireEvent.press(screen.getByRole("button", { name: "Review recipe" }));
  await waitFor(() => expect(screen.getByText("The service is temporarily unavailable. Please try again later.")).toBeTruthy());
  expect(screen.queryByText("private provider details")).toBeNull();
});
