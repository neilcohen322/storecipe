import type { ComponentProps } from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

import type { Recipe } from "../../api/catalog";
import { activateCookingKeepAwake } from "../../cooking/keepAwake";
import { createDefaultSession, loadCookingSession } from "../../cooking/session";
import { CookingScreen } from "../CookingScreen";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

jest.mock("../../cooking/keepAwake", () => ({
  activateCookingKeepAwake: jest.fn(async () => true),
  releaseCookingKeepAwake: jest.fn(async () => undefined),
}));

jest.mock("../../cooking/session", () => {
  const actual = jest.requireActual("../../cooking/session") as typeof import("../../cooking/session");
  return {
    ...actual,
    loadCookingSession: jest.fn(),
  };
});

jest.mock("../../cooking/timerAudio", () => ({
  armTimerAudio: jest.fn(() => ({ playExpiry: jest.fn() })),
}));

const recipe: Recipe = {
  id: "recipe-1",
  title: "Lemon pasta",
  sourceUrl: null,
  servings: 4,
  prepMinutes: 10,
  cookMinutes: 15,
  totalMinutes: 25,
  ingredients: [{ rawText: "200g spaghetti", name: "spaghetti", canonicalName: "spaghetti", quantity: 200, unit: "g" }],
  instructions: ["Boil the pasta for 10 minutes.", "Toss with lemon."],
  tags: ["quick"],
  rating: 3,
  coverImage: null,
  favorite: false,
  personalNotes: null,
  lastCookedAt: null,
};

beforeEach(() => {
  (loadCookingSession as jest.MockedFunction<typeof loadCookingSession>).mockReset();
  (activateCookingKeepAwake as jest.MockedFunction<typeof activateCookingKeepAwake>).mockResolvedValue(true);
});

test("renders a reachable cooking session with timer and keep-awake controls", async () => {
  const getRecipe = jest.fn().mockResolvedValue(recipe);
  const catalog = { getRecipe } as unknown as ComponentProps<typeof CookingScreen>["catalog"];
  const screen = await render(
    <CookingScreen recipeId="recipe-1" catalog={catalog} onBack={jest.fn()} onUnauthorized={jest.fn()} />,
  );

  await waitFor(() => expect(screen.getByRole("header", { name: "Cooking Lemon pasta" })).toBeTruthy());
  const ingredient = screen.getByLabelText("200g spaghetti");
  expect(ingredient).toBeTruthy();
  expect(ingredient.props.accessibilityRole).toBe("checkbox");
  expect(ingredient.props.accessibilityState).toMatchObject({ checked: false });
  await fireEvent.press(ingredient);
  await waitFor(() => expect(screen.getByLabelText("200g spaghetti").props.accessibilityState).toMatchObject({ checked: true }));
  expect(screen.getByText("Boil the pasta for 10 minutes.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Start timer" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Keep screen awake" })).toBeTruthy();

  await fireEvent.press(screen.getByRole("button", { name: "Next step" }));
  await waitFor(() => expect(screen.getByText("Toss with lemon.")).toBeTruthy());
});

test("reactivates keep-awake when a saved cooking session is reloaded", async () => {
  const loadSession = loadCookingSession as jest.MockedFunction<typeof loadCookingSession>;
  loadSession.mockReturnValue({
    ...createDefaultSession("recipe-1", { kind: "servings", servings: 4 }),
    keepAwake: true,
  });
  const getRecipe = jest.fn().mockResolvedValue(recipe);
  const catalog = { getRecipe } as unknown as ComponentProps<typeof CookingScreen>["catalog"];
  const screen = await render(
    <CookingScreen recipeId="recipe-1" catalog={catalog} onBack={jest.fn()} onUnauthorized={jest.fn()} />,
  );

  await waitFor(() => expect(screen.getByRole("button", { name: "Release keep awake" })).toBeTruthy());
  expect(activateCookingKeepAwake).toHaveBeenCalled();
});
