import { act, render, waitFor } from "@testing-library/react-native";
import { Text } from "react-native";

import { AuthenticatedRecipeImage } from "../AuthenticatedRecipeImage";

const created: string[] = [];
const revoked: string[] = [];

beforeEach(() => {
  created.length = 0;
  revoked.length = 0;
  let next = 0;
  globalThis.URL.createObjectURL = ((blob: Blob) => {
    const url = `blob:cover-${next++}-${blob.size}`;
    created.push(url);
    return url;
  }) as typeof URL.createObjectURL;
  globalThis.URL.revokeObjectURL = ((url: string) => {
    revoked.push(url);
  }) as typeof URL.revokeObjectURL;
});

const fallback = <Text>placeholder</Text>;

test("keeps the placeholder until bytes succeed and labels the cover", async () => {
  let resolve!: (value: { blob: Blob; etag: string; notModified: boolean }) => void;
  const pending = new Promise<{ blob: Blob; etag: string; notModified: boolean }>((next) => {
    resolve = next;
  });
  const screen = await render(
    <AuthenticatedRecipeImage
      recipeId="recipe-1"
      title="Lemon pasta"
      etag={"a".repeat(64)}
      url="/v1/recipes/recipe-1/cover-image"
      loadCoverImage={() => pending}
      fallback={fallback}
    />,
  );
  expect(screen.getByText("placeholder")).toBeTruthy();
  await act(async () => resolve({ blob: new Blob(["RIFF"]), etag: "a".repeat(64), notModified: false }));
  await waitFor(() => expect(screen.getByLabelText("Cover image for Lemon pasta")).toBeTruthy());
});

test("first load is unconditional; 304 is used only after bytes are cached", async () => {
  const blob = new Blob(["RIFF"]);
  const loadCoverImage = jest
    .fn()
    .mockResolvedValueOnce({ blob, etag: "a".repeat(64), notModified: false })
    .mockResolvedValueOnce({ blob: null, etag: "a".repeat(64), notModified: true });
  const screen = await render(
    <AuthenticatedRecipeImage
      recipeId="recipe-1"
      title="Lemon pasta"
      etag={"a".repeat(64)}
      url="/v1/recipes/recipe-1/cover-image"
      loadCoverImage={loadCoverImage}
      fallback={fallback}
    />,
  );
  await waitFor(() => expect(screen.getByLabelText("Cover image for Lemon pasta")).toBeTruthy());
  expect(loadCoverImage.mock.calls[0][0].etag).toBeUndefined();
  screen.rerender(
    <AuthenticatedRecipeImage
      recipeId="recipe-1"
      title="Lemon pasta"
      etag={"a".repeat(64)}
      url="/v1/recipes/recipe-1/cover-image?reload=1"
      loadCoverImage={loadCoverImage}
      fallback={fallback}
    />,
  );
  await waitFor(() => expect(loadCoverImage).toHaveBeenCalledTimes(2));
  expect(loadCoverImage.mock.calls[1][0].etag).toBe("a".repeat(64));
  expect(screen.getByLabelText("Cover image for Lemon pasta")).toBeTruthy();
  expect(created).toHaveLength(1);
  await act(async () => {
    screen.unmount();
  });
  expect(revoked.length).toBeGreaterThan(0);
});

test("304 without cached bytes stays on the placeholder", async () => {
  const loadCoverImage = jest.fn().mockResolvedValue({ blob: null, etag: "a".repeat(64), notModified: true });
  const screen = await render(
    <AuthenticatedRecipeImage
      recipeId="recipe-1"
      title="Lemon pasta"
      etag={"a".repeat(64)}
      url="/v1/recipes/recipe-1/cover-image"
      loadCoverImage={loadCoverImage}
      fallback={fallback}
    />,
  );
  await waitFor(() => expect(loadCoverImage).toHaveBeenCalledTimes(1));
  expect(loadCoverImage.mock.calls[0][0].etag).toBeUndefined();
  expect(screen.getByText("placeholder")).toBeTruthy();
  expect(screen.queryByLabelText("Cover image for Lemon pasta")).toBeNull();
});

test("a replacement etag fetches unconditionally", async () => {
  const loadCoverImage = jest
    .fn()
    .mockResolvedValueOnce({ blob: new Blob(["old"]), etag: "a".repeat(64), notModified: false })
    .mockResolvedValueOnce({ blob: new Blob(["new"]), etag: "b".repeat(64), notModified: false });
  const screen = await render(
    <AuthenticatedRecipeImage
      recipeId="recipe-1"
      title="Lemon pasta"
      etag={"a".repeat(64)}
      url="/v1/recipes/recipe-1/cover-image"
      loadCoverImage={loadCoverImage}
      fallback={fallback}
    />,
  );
  await waitFor(() => expect(screen.getByLabelText("Cover image for Lemon pasta")).toBeTruthy());
  screen.rerender(
    <AuthenticatedRecipeImage
      recipeId="recipe-1"
      title="Lemon pasta"
      etag={"b".repeat(64)}
      url="/v1/recipes/recipe-1/cover-image"
      loadCoverImage={loadCoverImage}
      fallback={fallback}
    />,
  );
  await waitFor(() => expect(loadCoverImage).toHaveBeenCalledTimes(2));
  expect(loadCoverImage.mock.calls[0][0].etag).toBeUndefined();
  expect(loadCoverImage.mock.calls[1][0].etag).toBeUndefined();
});

test("falls back when loading fails and cancels stale requests", async () => {
  const loadCoverImage = jest.fn().mockRejectedValue(new Error("offline"));
  const screen = await render(
    <AuthenticatedRecipeImage
      recipeId="recipe-1"
      title="Lemon pasta"
      etag={"a".repeat(64)}
      url="/v1/recipes/recipe-1/cover-image"
      loadCoverImage={loadCoverImage}
      fallback={fallback}
    />,
  );
  await waitFor(() => expect(screen.getByText("placeholder")).toBeTruthy());
  expect(screen.queryByLabelText("Cover image for Lemon pasta")).toBeNull();
});
