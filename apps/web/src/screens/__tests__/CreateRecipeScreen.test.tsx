import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react-native";

import { ApiUnauthorizedError } from "../../api/client";
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
const catalogWith = (createRecipe: jest.Mock) => ({ createRecipe }) as unknown as React.ComponentProps<typeof CreateRecipeScreen>["catalog"];

async function renderScreen(createRecipe = jest.fn().mockResolvedValue(recipe), layoutMode: "compact" | "medium" | "expanded" = "medium") {
  return render(<CreateRecipeScreen catalog={catalogWith(createRecipe)} {...actions} layoutMode={layoutMode} />);
}

async function fillValidRecipe(screen: Awaited<ReturnType<typeof renderScreen>>, title = "  Soup  ") {
  await fireEvent.changeText(screen.getByLabelText("Title"), title);
  await fireEvent.changeText(screen.getByLabelText("Ingredients"), " water \n\n salt ");
  await fireEvent.changeText(screen.getByLabelText("Instructions"), " boil \n serve ");
}

beforeEach(() => jest.clearAllMocks());
afterEach(cleanup);

test("shows inline required-field errors without losing entered content", async () => {
  const screen = await renderScreen();
  await fireEvent.press(screen.getByRole("button", { name: "Create recipe" }));
  expect(screen.getByText("Title is required.")).toBeTruthy();
  expect(screen.getByText("Add at least one ingredient.")).toBeTruthy();
  expect(screen.getByText("Add at least one instruction.")).toBeTruthy();
  expect(screen.getByLabelText("Title").props.accessibilityState).toMatchObject({ invalid: true });
});

test("submits normalized content from the keyboard once and routes to the recipe detail", async () => {
  const createRecipe = jest.fn().mockResolvedValue(recipe);
  const screen = await renderScreen(createRecipe);
  await fillValidRecipe(screen);
  await fireEvent(screen.getByLabelText("Instructions"), "submitEditing");
  await waitFor(() => expect(createRecipe).toHaveBeenCalledWith({ title: "Soup", ingredients: [{ rawText: "water", name: "water" }, { rawText: "salt", name: "salt" }], instructions: ["boil", "serve"] }, expect.any(String)));
  await waitFor(() => expect(actions.onCreated).toHaveBeenCalledWith("recipe-1"));
});

test("prevents duplicate click and keyboard submissions while a request is in flight", async () => {
  const pending = deferred<Recipe>();
  const createRecipe = jest.fn(() => pending.promise);
  const screen = await renderScreen(createRecipe);
  await fillValidRecipe(screen);
  await fireEvent.press(screen.getByRole("button", { name: "Create recipe" }));
  await fireEvent(screen.getByLabelText("Instructions"), "submitEditing");
  await fireEvent.press(screen.getByRole("button", { name: "Create recipe" }));
  expect(createRecipe).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve(recipe); await pending.promise; });
  await waitFor(() => expect(actions.onCreated).toHaveBeenCalledWith("recipe-1"));
});

test("retains a failed attempt key for unchanged and whitespace-normalized retries", async () => {
  const createRecipe = jest.fn().mockRejectedValueOnce(new Error("transport details")).mockResolvedValueOnce(recipe);
  const screen = await renderScreen(createRecipe);
  await fillValidRecipe(screen);
  await fireEvent.press(screen.getByRole("button", { name: "Create recipe" }));
  await waitFor(() => expect(screen.getByText("We couldn't create your recipe. Please try again.")).toBeTruthy());
  expect(screen.queryByText("transport details")).toBeNull();
  await fireEvent.changeText(screen.getByLabelText("Ingredients"), "\n water \n\n salt \n");
  await fireEvent.press(screen.getByRole("button", { name: "Create recipe" }));
  await waitFor(() => expect(createRecipe).toHaveBeenCalledTimes(2));
  expect(createRecipe.mock.calls[1]?.[1]).toBe(createRecipe.mock.calls[0]?.[1]);
  await waitFor(() => expect(actions.onCreated).toHaveBeenCalledWith("recipe-1"));
});

test("rotates a retained key after a meaningful normalized edit", async () => {
  const createRecipe = jest.fn().mockRejectedValueOnce(new Error("failure")).mockResolvedValueOnce(recipe);
  const screen = await renderScreen(createRecipe);
  await fillValidRecipe(screen);
  await fireEvent.press(screen.getByRole("button", { name: "Create recipe" }));
  await waitFor(() => expect(createRecipe).toHaveBeenCalledTimes(1));
  await fireEvent.changeText(screen.getByLabelText("Instructions"), "boil\nserve with bread");
  await fireEvent.press(screen.getByRole("button", { name: "Create recipe" }));
  await waitFor(() => expect(createRecipe).toHaveBeenCalledTimes(2));
  expect(createRecipe.mock.calls[1]?.[1]).not.toBe(createRecipe.mock.calls[0]?.[1]);
  await waitFor(() => expect(actions.onCreated).toHaveBeenCalledWith("recipe-1"));
});

test("uses the compact sticky submit on compact layouts", async () => {
  const compact = await renderScreen(jest.fn(), "compact");
  expect(compact.getByTestId("create-recipe-sticky-submit")).toBeTruthy();
  expect(compact.queryByTestId("create-recipe-header-submit")).toBeNull();
});

test("uses the page-header submit on medium layouts", async () => {
  const medium = await renderScreen(jest.fn(), "medium");
  expect(medium.getByTestId("create-recipe-header-submit")).toBeTruthy();
  expect(medium.queryByTestId("create-recipe-sticky-submit")).toBeNull();
});

test("retains content and redirects unauthorized responses without exposing details", async () => {
  const screen = await renderScreen(jest.fn().mockRejectedValue(new ApiUnauthorizedError("private provider details")));
  await fillValidRecipe(screen, "Minestrone");
  await fireEvent.press(screen.getByRole("button", { name: "Create recipe" }));
  await waitFor(() => expect(actions.onUnauthorized).toHaveBeenCalledTimes(1));
  expect(screen.getByLabelText("Title").props.value).toBe("Minestrone");
  expect(screen.queryByText("private provider details")).toBeNull();
});
