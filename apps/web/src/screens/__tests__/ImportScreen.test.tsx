import { fireEvent, render, waitFor } from "@testing-library/react-native";

import type { createIngestionApi } from "../../api/ingestion";
import { ImportSessionProvider } from "../../imports/ImportSessionProvider";
import { ImportScreen } from "../ImportScreen";

function ingestionWith(overrides: Partial<ReturnType<typeof createIngestionApi>> = {}) {
  return {
    createUrlImport: jest.fn().mockResolvedValue({ jobId: "job-1" }),
    createTextImport: jest.fn().mockResolvedValue({ jobId: "job-1" }),
    getImport: jest.fn().mockResolvedValue({ id: "job-1", status: "processing", attemptCount: 0, createdRecipeId: null, errorCategory: null, cancellationRequested: false }),
    ...overrides,
  } as unknown as ReturnType<typeof createIngestionApi>;
}

async function renderScreen(ingestion = ingestionWith()) {
  return await render(<ImportSessionProvider ingestion={ingestion} onUnauthorized={jest.fn()}><ImportScreen onBack={jest.fn()} /></ImportSessionProvider>);
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
  const ingestion = ingestionWith({ getImport: jest.fn().mockResolvedValue({ id: "job-1", status: "timed_out", attemptCount: 1, createdRecipeId: null, errorCategory: "import_deadline_exceeded", cancellationRequested: false }) });
  const screen = await renderScreen(ingestion);
  await fireEvent.changeText(screen.getByLabelText("Recipe URL"), "https://example.com/soup");
  await fireEvent.press(screen.getByRole("button", { name: "Start import" }));

  await waitFor(() => expect(screen.getByText("This import took too long and stopped.")).toBeTruthy());
  expect(screen.getByRole("button", { name: "Retry import" })).toBeTruthy();
  expect(screen.queryByText("import_deadline_exceeded")).toBeNull();
});
