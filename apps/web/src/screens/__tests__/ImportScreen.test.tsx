import { fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({ useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }) }));

import type { createIngestionApi } from "../../api/ingestion";
import { ImportSessionProvider } from "../../imports/ImportSessionProvider";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { ImportScreen } from "../ImportScreen";

function ingestionWith(overrides: Partial<ReturnType<typeof createIngestionApi>> = {}) {
  return {
    createUrlImport: jest.fn().mockResolvedValue({ jobId: "job-1" }),
    createTextImport: jest.fn().mockResolvedValue({ jobId: "job-1" }),
    getImport: jest.fn().mockResolvedValue({ id: "job-1", status: "processing", attemptCount: 0, createdRecipeId: null, errorCategory: null, cancellationRequested: false, hasCandidate: false }),
    ...overrides,
  } as unknown as ReturnType<typeof createIngestionApi>;
}

async function renderScreen(ingestion = ingestionWith(), onContinueExtractedRecipe = jest.fn()) {
  return await render(<ThemeProvider systemSchemeOverride="light"><ImportSessionProvider ingestion={ingestion} onUnauthorized={jest.fn()}><ImportScreen onBack={jest.fn()} onContinueExtractedRecipe={onContinueExtractedRecipe} /></ImportSessionProvider></ThemeProvider>);
}

test("switches source modes and validates the active normalized source", async () => {
  const screen = await renderScreen();
  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));
  expect(screen.getByText("URL is required.")).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Text" }));
  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));
  expect(screen.getByText("Recipe text is required.")).toBeTruthy();
});

test("submits one normalized text import and shows only coarse working status", async () => {
  const ingestion = ingestionWith();
  const screen = await renderScreen(ingestion);
  await fireEvent.press(screen.getByRole("button", { name: "Text" }));
  await fireEvent.changeText(screen.getByLabelText("Recipe text"), "  soup  ");
  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));
  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));

  await waitFor(() => expect(ingestion.createTextImport).toHaveBeenCalledTimes(1));
  expect(ingestion.createTextImport).toHaveBeenCalledWith("soup", expect.objectContaining({ idempotencyKey: expect.any(String) }));
  await waitFor(() => expect(screen.getByText("Import in progress")).toBeTruthy());
  expect(screen.getByText("Some imports may take longer than others.")).toBeTruthy();
  expect(screen.queryByText(/fetching|rendering|extracting|saving/i)).toBeNull();
});

test("uses distinct safe terminal copy and only exposes retry for safe failures", async () => {
  const ingestion = ingestionWith({ getImport: jest.fn().mockResolvedValue({ id: "job-1", status: "timed_out", attemptCount: 1, createdRecipeId: null, errorCategory: "import_deadline_exceeded", cancellationRequested: false, hasCandidate: false }) });
  const screen = await renderScreen(ingestion);
  await fireEvent.changeText(screen.getByLabelText("Recipe URL"), "https://example.com/soup");
  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));

  await waitFor(() => expect(screen.getByText("This import took too long and stopped.")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Retry import" })).toBeTruthy();
  expect(screen.queryByText("import_deadline_exceeded")).toBeNull();
});

test("offers the extracted recipe when automatic import cannot finish saving", async () => {
  const onContinueExtractedRecipe = jest.fn();
  const ingestion = ingestionWith({ getImport: jest.fn().mockResolvedValue({ id: "job-1", status: "review_required", attemptCount: 1, createdRecipeId: null, errorCategory: "provider_invalid_output", cancellationRequested: false, hasCandidate: true }) });
  const screen = await renderScreen(ingestion, onContinueExtractedRecipe);
  await fireEvent.changeText(screen.getByLabelText("Recipe URL"), "https://example.com/soup");
  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));

  await waitFor(() => expect(screen.getByText("The recipe was extracted but couldn't be saved automatically. Continue with the extracted recipe to check it and save.")).toBeTruthy());
  expect(screen.queryByText(/needs your review/i)).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Continue with extracted recipe" }));
  expect(onContinueExtractedRecipe).toHaveBeenCalledWith("job-1");
  expect(screen.getByRole("button", { name: "Retry import" })).toBeTruthy();
  expect(screen.queryByText("provider_invalid_output")).toBeNull();
});

test("does not offer a missing extract when the daily AI budget is exhausted", async () => {
  const onContinueExtractedRecipe = jest.fn();
  const ingestion = ingestionWith({ getImport: jest.fn().mockResolvedValue({ id: "job-1", status: "review_required", attemptCount: 1, createdRecipeId: null, errorCategory: "daily_ai_budget_exceeded", cancellationRequested: false, hasCandidate: false }) });
  const screen = await renderScreen(ingestion, onContinueExtractedRecipe);
  await fireEvent.changeText(screen.getByLabelText("Recipe URL"), "https://example.com/soup");
  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));

  await waitFor(() => expect(screen.getByText("Today's AI budget is used up, so this recipe wasn't extracted. Try again later, or paste the recipe as text.")).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Continue with extracted recipe" })).toBeNull();
  expect(screen.getByRole("button", { name: "Retry import" })).toBeTruthy();
  expect(screen.queryByText("daily_ai_budget_exceeded")).toBeNull();
});
