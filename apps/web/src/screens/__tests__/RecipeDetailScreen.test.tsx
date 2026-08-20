import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

import { ApiError, ApiNetworkError, ApiUnauthorizedError } from "../../api/client";
import type { Recipe } from "../../api/catalog";
import { RecipeDetailScreen } from "../RecipeDetailScreen";
import { pickRecipeCoverImage } from "../../media/imagePicker";

jest.mock("../../media/imagePicker", () => ({
  pickRecipeCoverImage: jest.fn(),
  blobFromPickerUri: jest.fn(async () => new Blob(["RIFF"])),
  pickerStatusMessage: (status: string) => {
    if (status === "too_large") return "Choose an image smaller than 8 MB.";
    if (status === "unsupported") return "Choose a valid JPEG, PNG, or WebP image.";
    return null;
  },
  coverImageErrorMessage: (error: { status?: number }) => {
    if (error?.status === 413) return "Choose an image smaller than 8 MB.";
    if (error?.status === 422) return "Choose a valid JPEG, PNG, or WebP image.";
    if (error?.status === 503) return "Images are temporarily unavailable. Your recipe is safe.";
    return "We couldn't upload the image. Please try again.";
  },
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

const recipe: Recipe = {
  id: "recipe-1", title: "Lemon pasta", sourceUrl: null, servings: 4, prepMinutes: 10, cookMinutes: 15, totalMinutes: 25,
  ingredients: [
    { rawText: "200g spaghetti", name: "spaghetti", canonicalName: "spaghetti" },
    { rawText: "1 lemon", name: "lemon", canonicalName: "lemon" },
  ],
  instructions: ["Boil the pasta.", "Toss with lemon."], tags: ["quick", "pasta"], rating: 3, coverImage: null,
};
const secondRecipe: Recipe = { ...recipe, id: "recipe-2", title: "Tomato risotto", rating: 2 };
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void; const promise = new Promise<T>((next, fail) => { resolve = next; reject = fail; }); return { promise, resolve, reject }; }
function catalogWith(getRecipe: jest.Mock, putRating = jest.fn(), extras: Record<string, jest.Mock> = {}) {
  return {
    getRecipe,
    putRating,
    getCoverImage: extras.getCoverImage ?? jest.fn().mockResolvedValue({ blob: null, etag: null, notModified: false }),
    uploadCoverImage: extras.uploadCoverImage ?? jest.fn(),
    deleteCoverImage: extras.deleteCoverImage ?? jest.fn(),
  } as unknown as React.ComponentProps<typeof RecipeDetailScreen>["catalog"];
}
const actions = { onBack: jest.fn(), onUnauthorized: jest.fn() };
const renderScreen = (getRecipe: jest.Mock, putRating?: jest.Mock, recipeId: unknown = "recipe-1") => render(<RecipeDetailScreen recipeId={recipeId} catalog={catalogWith(getRecipe, putRating)} {...actions} />);

beforeEach(() => jest.clearAllMocks());

test("rejects missing and malformed route IDs without requesting a recipe", async () => {
  const getRecipe = jest.fn();
  const screen = await renderScreen(getRecipe, undefined, ["recipe-1"]);
  expect(screen.getByText("We couldn't find that recipe.")).toBeTruthy();
  expect(getRecipe).not.toHaveBeenCalled();
  await screen.rerender(<RecipeDetailScreen recipeId="  " catalog={catalogWith(getRecipe)} {...actions} />);
  expect(screen.getByText("We couldn't find that recipe.")).toBeTruthy();
});

test("shows a stable loading state then recipe media, metadata, semantic ingredients, and numbered instructions", async () => {
  const loading = deferred<Recipe>();
  const screen = await renderScreen(jest.fn(() => loading.promise));
  expect(screen.getByLabelText("Loading recipe")).toBeTruthy();
  await act(async () => loading.resolve(recipe));
  await waitFor(() => expect(screen.getByRole("header", { name: "Lemon pasta" })).toBeTruthy());
  expect(screen.getByTestId("recipe-detail-media")).toBeTruthy();
  expect(screen.getByText("Serves 4 · 25 min")).toBeTruthy();
  expect(screen.getByLabelText("Ingredients")).toBeTruthy();
  expect(screen.getByLabelText("200g spaghetti")).toBeTruthy();
  expect(screen.getByLabelText("1 lemon")).toBeTruthy();
  expect(screen.getByLabelText("Instructions")).toBeTruthy();
  expect(screen.getByText("Boil the pasta.")).toBeTruthy();
  expect(screen.getByText("Toss with lemon.")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Rate 3 out of 5" }).props.accessibilityState).toMatchObject({ selected: true });
});

test("distinguishes not found, offline, retryable, and unauthorized detail responses without exposing raw errors", async () => {
  const getRecipe = jest.fn().mockRejectedValueOnce(Object.assign(new Error("provider details"), { status: 404 })).mockRejectedValueOnce(new ApiNetworkError({ code: "ERR_NETWORK" })).mockRejectedValueOnce(new Error("provider details")).mockRejectedValueOnce(new ApiUnauthorizedError());
  const screen = await renderScreen(getRecipe);
  await waitFor(() => expect(screen.getByText("We couldn't find that recipe.")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByText("You’re offline. Check your connection and try again.")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByText("We couldn't load this recipe. Please try again.")).toBeTruthy());
  expect(screen.queryByText("provider details")).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(actions.onUnauthorized).toHaveBeenCalledTimes(1));
});

test("optimistically updates only the selected rating and disables duplicate submissions", async () => {
  const saving = deferred<{ value: number }>();
  const putRating = jest.fn(() => saving.promise);
  const screen = await renderScreen(jest.fn().mockResolvedValue(recipe), putRating);
  await waitFor(() => expect(screen.getByText("Lemon pasta")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Rate 5 out of 5" }));
  expect(screen.getByRole("button", { name: "Rate 5 out of 5" }).props.accessibilityState).toMatchObject({ selected: true, disabled: true });
  expect(screen.getByRole("button", { name: "Rate 3 out of 5" }).props.accessibilityState).toMatchObject({ selected: false, disabled: true });
  await fireEvent.press(screen.getByRole("button", { name: "Rate 5 out of 5" }));
  expect(putRating).toHaveBeenCalledTimes(1);
  await act(async () => saving.resolve({ value: 5 }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Rate 5 out of 5" }).props.accessibilityState).toMatchObject({ selected: true, disabled: false }));
});

test("rolls a failed rating back and offers a safe inline retry", async () => {
  const putRating = jest.fn().mockRejectedValueOnce(new Error("raw service error")).mockResolvedValueOnce({ value: 4 });
  const screen = await renderScreen(jest.fn().mockResolvedValue(recipe), putRating);
  await waitFor(() => expect(screen.getByText("Lemon pasta")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Rate 4 out of 5" }));
  await waitFor(() => expect(screen.getByText("We couldn't save your rating.")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Rate 3 out of 5" }).props.accessibilityState).toMatchObject({ selected: true });
  expect(screen.queryByText("raw service error")).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Try rating again" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Rate 4 out of 5" }).props.accessibilityState).toMatchObject({ selected: true }));
  expect(putRating).toHaveBeenCalledTimes(2);
});

test("rolls back an unauthorized rating before invoking a handler that keeps the screen mounted", async () => {
  const onUnauthorized = jest.fn();
  const screen = await render(<RecipeDetailScreen recipeId="recipe-1" catalog={catalogWith(jest.fn().mockResolvedValue(recipe), jest.fn().mockRejectedValue(new ApiUnauthorizedError()))} onBack={actions.onBack} onUnauthorized={onUnauthorized} />);
  await waitFor(() => expect(screen.getByText("Lemon pasta")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Rate 5 out of 5" }));
  await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: "Rate 3 out of 5" }).props.accessibilityState).toMatchObject({ selected: true });
  expect(screen.getByRole("button", { name: "Rate 5 out of 5" }).props.accessibilityState).toMatchObject({ selected: false });
});

test("suppresses stale load and rating responses after navigation and unmount", async () => {
  const firstLoad = deferred<Recipe>(); const secondLoad = deferred<Recipe>(); const staleRating = deferred<{ value: number }>();
  const getRecipe = jest.fn().mockReturnValueOnce(firstLoad.promise).mockReturnValueOnce(secondLoad.promise);
  const putRating = jest.fn(() => staleRating.promise);
  const screen = await renderScreen(getRecipe, putRating);
  await screen.rerender(<RecipeDetailScreen recipeId="recipe-2" catalog={catalogWith(getRecipe, putRating)} {...actions} />);
  await act(async () => secondLoad.resolve(secondRecipe));
  await waitFor(() => expect(screen.getByText("Tomato risotto")).toBeTruthy());
  await act(async () => firstLoad.resolve(recipe));
  expect(screen.queryByText("Lemon pasta")).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Rate 5 out of 5" }));
  await screen.unmount();
  await act(async () => staleRating.reject(new ApiUnauthorizedError()));
  expect(actions.onUnauthorized).not.toHaveBeenCalled();
});

test("does not let a rating response from the previous route affect the current recipe", async () => {
  const staleRating = deferred<{ value: number }>();
  const getRecipe = jest.fn().mockResolvedValueOnce(recipe).mockResolvedValueOnce(secondRecipe);
  const putRating = jest.fn(() => staleRating.promise);
  const screen = await renderScreen(getRecipe, putRating);
  await waitFor(() => expect(screen.getByText("Lemon pasta")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Rate 5 out of 5" }));
  await screen.rerender(<RecipeDetailScreen recipeId="recipe-2" catalog={catalogWith(getRecipe, putRating)} {...actions} />);
  await waitFor(() => expect(screen.getByText("Tomato risotto")).toBeTruthy());
  await act(async () => staleRating.reject(new ApiUnauthorizedError()));
  expect(screen.getByText("Tomato risotto")).toBeTruthy();
  expect(actions.onUnauthorized).not.toHaveBeenCalled();
});

test("adds, retries, and removes a cover image with safe copy", async () => {
  const covered = {
    ...recipe,
    coverImage: { url: "/v1/recipes/recipe-1/cover-image", etag: "a".repeat(64), byteSize: 8, contentType: "image/webp" as const },
  };
  const uploadCoverImage = jest.fn()
    .mockRejectedValueOnce(new ApiError("Images are temporarily unavailable. Your recipe is safe.", 503, "media_unavailable"))
    .mockResolvedValueOnce(covered.coverImage);
  const deleteCoverImage = jest.fn().mockResolvedValue(undefined);
  (pickRecipeCoverImage as jest.Mock).mockResolvedValue({
    status: "selected", uri: "blob:cover", mimeType: "image/jpeg", fileName: "cover.jpg", fileSize: 12,
  });
  const screen = await render(
    <RecipeDetailScreen
      recipeId="recipe-1"
      catalog={catalogWith(jest.fn().mockResolvedValue(recipe), jest.fn(), { uploadCoverImage, deleteCoverImage })}
      {...actions}
    />,
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "Add cover image" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Add cover image" }));
  await waitFor(() => expect(screen.getByText("Images are temporarily unavailable. Your recipe is safe.")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Try image upload again" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Replace cover image" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Remove cover image" }));
  await fireEvent.press(screen.getByRole("button", { name: "Confirm" }));
  await waitFor(() => expect(deleteCoverImage).toHaveBeenCalledWith("recipe-1"));
  await waitFor(() => expect(screen.getByRole("button", { name: "Add cover image" })).toBeTruthy());
});

test("keeps the current cover while a replacement fails and cancels removal", async () => {
  const covered = {
    ...recipe,
    coverImage: { url: "/v1/recipes/recipe-1/cover-image", etag: "a".repeat(64), byteSize: 8, contentType: "image/webp" as const },
  };
  const uploadCoverImage = jest.fn()
    .mockRejectedValueOnce(new ApiError("Choose an image smaller than 8 MB.", 413, "image_too_large"))
    .mockRejectedValueOnce(new ApiError("Choose a valid JPEG, PNG, or WebP image.", 422, "invalid_image"));
  (pickRecipeCoverImage as jest.Mock).mockResolvedValue({
    status: "selected", uri: "blob:cover", mimeType: "image/jpeg", fileName: "cover.jpg", fileSize: 12,
  });
  const screen = await render(
    <RecipeDetailScreen
      recipeId="recipe-1"
      catalog={catalogWith(jest.fn().mockResolvedValue(covered), jest.fn(), { uploadCoverImage })}
      {...actions}
    />,
  );
  await waitFor(() => expect(screen.getByRole("button", { name: "Replace cover image" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Replace cover image" }));
  await waitFor(() => expect(screen.getByText("Choose an image smaller than 8 MB.")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Replace cover image" })).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Try image upload again" }));
  await waitFor(() => expect(screen.getByText("Choose a valid JPEG, PNG, or WebP image.")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Remove cover image" }));
  await fireEvent.press(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Replace cover image" })).toBeTruthy();
});
