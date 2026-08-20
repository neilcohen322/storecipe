import { fireEvent, render, waitFor } from "@testing-library/react-native";
import { Platform, StyleSheet } from "react-native";

import type { Recipe } from "../../api/catalog";
import { RecipeCard } from "../RecipeCard";

jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

const recipe: Recipe = {
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
  coverImage: null,
};

test("opens a recipe from an accessible stable card with deterministic theme media", async () => {
  const onOpen = jest.fn();
  const first = await render(<RecipeCard item={recipe} onOpen={onOpen} view="card" />);
  const firstMedia = first.getByTestId("recipe-card-media-recipe-1");
  const initialMediaStyle = firstMedia.props.style;

  fireEvent.press(first.getByRole("button", { name: "Open Lemon pasta" }));

  expect(onOpen).toHaveBeenCalledWith("recipe-1");
  expect(initialMediaStyle).toEqual(expect.arrayContaining([expect.objectContaining({ backgroundColor: "#b7791f" })]));
  expect(StyleSheet.flatten(initialMediaStyle)).toEqual(expect.objectContaining({ minHeight: 240, width: "100%" }));
  expect(first.getByText("25 min · 4/5")).toBeTruthy();
  expect(StyleSheet.flatten(first.getByText("Lemon pasta").props.style).color).toBe("#ffffff");
});

test("loads a private cover when metadata and a loader are present", async () => {
  globalThis.URL.createObjectURL = jest.fn(() => "blob:cover-card") as typeof URL.createObjectURL;
  globalThis.URL.revokeObjectURL = jest.fn() as typeof URL.revokeObjectURL;
  const loadCoverImage = jest.fn().mockResolvedValue({ blob: new Blob(["RIFF"]), etag: "a".repeat(64), notModified: false });
  const covered = {
    ...recipe,
    coverImage: { url: "/v1/recipes/recipe-1/cover-image", etag: "a".repeat(64), byteSize: 8, contentType: "image/webp" as const },
  };
  const screen = await render(<RecipeCard item={covered} onOpen={jest.fn()} view="card" loadCoverImage={loadCoverImage} />);
  await waitFor(() => expect(screen.getByLabelText("Cover image for Lemon pasta")).toBeTruthy());
});

test("uses the Pressable activation path without a custom keyboard handler", async () => {
  const originalPlatform = Platform.OS;
  Object.defineProperty(Platform, "OS", { configurable: true, value: "web" });
  try {
    const onOpen = jest.fn();
    const screen = await render(<RecipeCard item={recipe} onOpen={onOpen} view="list" />);
    const card = screen.getByRole("button", { name: "Open Lemon pasta" });

    expect(card.props.focusable).toBe(true);
    expect(card.props.onKeyDown).toBeUndefined();
    expect(StyleSheet.flatten(screen.getByText("Lemon pasta").props.style).color).toBe("#1c1410");
    expect(screen.getByText("weeknight · pasta")).toBeTruthy();
    await fireEvent.press(card);
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onOpen).toHaveBeenCalledWith("recipe-1");
  } finally {
    Object.defineProperty(Platform, "OS", { configurable: true, value: originalPlatform });
  }
});
