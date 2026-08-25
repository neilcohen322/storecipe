import { fireEvent, render, waitFor } from "@testing-library/react-native";

import { ApiError, ApiNetworkError, ApiUnauthorizedError } from "../../api/client";
import type { createIngestionApi, ImportHistoryItem } from "../../api/ingestion";
import { ImportHistoryScreen } from "../ImportHistoryScreen";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

jest.mock("expo-router", () => {
  const { useEffect } = require("react");
  return {
    useFocusEffect: (callback: () => void | (() => void)) => {
      useEffect(() => {
        const cleanup = callback();
        return typeof cleanup === "function" ? cleanup : undefined;
      }, [callback]);
    },
  };
});

const waiting: ImportHistoryItem = {
  id: "job-wait",
  inputKind: "url",
  createdAt: "2026-08-24T12:00:00.000Z",
  updatedAt: "2026-08-24T12:00:00.000Z",
  terminalAt: null,
  status: "queued",
  phase: "waiting",
};

const completed: ImportHistoryItem = {
  id: "job-done",
  inputKind: "text",
  createdAt: "2026-08-23T12:00:00.000Z",
  updatedAt: "2026-08-23T12:05:00.000Z",
  terminalAt: "2026-08-23T12:05:00.000Z",
  status: "completed",
  phase: "completed",
};

const failed: ImportHistoryItem = {
  ...completed,
  id: "job-failed",
  status: "failed",
  phase: "failed",
};

function ingestionWith(
  listImports: jest.Mock,
  cancelImport = jest.fn().mockResolvedValue(undefined),
) {
  return { listImports, cancelImport } as unknown as ReturnType<typeof createIngestionApi>;
}

const actions = { onNewImport: jest.fn(), onUnauthorized: jest.fn() };
const renderScreen = (listImports: jest.Mock, cancelImport?: jest.Mock) =>
  render(<ImportHistoryScreen ingestion={ingestionWith(listImports, cancelImport)} {...actions} />);

beforeEach(() => jest.clearAllMocks());

test("shows an honest empty history without retry or dismiss actions", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("No imports yet.")).toBeTruthy());
  expect(screen.getByText("Imports you start will appear here.")).toBeTruthy();
  expect(screen.queryByText(/recent imports/i)).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry import" })).toBeNull();
  expect(screen.queryByRole("button", { name: /dismiss/i })).toBeNull();
  expect(screen.getByRole("button", { name: "Import a recipe" })).toBeTruthy();
});

test("lists retained metadata with safe phase labels and no raw stages", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [waiting, completed], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("Waiting")).toBeTruthy());
  expect(screen.getByText("URL")).toBeTruthy();
  expect(screen.getByText("Text")).toBeTruthy();
  expect(screen.getByText("Complete")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Cancel import" })).toBeTruthy();
  expect(screen.queryByText("queued")).toBeNull();
  expect(screen.queryByText("MODEL_EXTRACTING")).toBeNull();
  expect(screen.queryByText(/percent/i)).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry import" })).toBeNull();
});

test("does not offer cancel for terminal imports including failures", async () => {
  const screen = await renderScreen(jest.fn().mockResolvedValue({ items: [failed], nextCursor: null }));
  await waitFor(() => expect(screen.getByText("Failed")).toBeTruthy());
  expect(screen.queryByRole("button", { name: "Cancel import" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Retry import" })).toBeNull();
  expect(screen.queryByRole("button", { name: /dismiss/i })).toBeNull();
});

test("cancels an in-progress import and refetches history", async () => {
  const cancelImport = jest.fn().mockResolvedValue(undefined);
  const listImports = jest.fn()
    .mockResolvedValueOnce({ items: [waiting], nextCursor: null })
    .mockResolvedValueOnce({ items: [{ ...waiting, status: "cancelled", phase: "cancelled", terminalAt: "2026-08-24T12:02:00.000Z" }], nextCursor: null });
  const screen = await renderScreen(listImports, cancelImport);
  await waitFor(() => expect(screen.getByRole("button", { name: "Cancel import" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Cancel import" }));
  await waitFor(() => expect(cancelImport).toHaveBeenCalledWith("job-wait"));
  await waitFor(() => expect(screen.getByText("Cancelled")).toBeTruthy());
  expect(listImports).toHaveBeenCalledTimes(2);
});

test("keeps a safe error when cancel is no longer possible", async () => {
  const cancelImport = jest.fn().mockRejectedValue(new ApiError("Import job is no longer queued.", 409));
  const listImports = jest.fn()
    .mockResolvedValueOnce({ items: [waiting], nextCursor: null })
    .mockResolvedValueOnce({ items: [completed], nextCursor: null });
  const screen = await renderScreen(listImports, cancelImport);
  await waitFor(() => expect(screen.getByRole("button", { name: "Cancel import" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Cancel import" }));
  await waitFor(() => expect(screen.getByText("This import can no longer be cancelled.")).toBeTruthy());
  expect(screen.queryByText("Import job is no longer queued.")).toBeNull();
});

test("load more appends the next page", async () => {
  const listImports = jest.fn()
    .mockResolvedValueOnce({ items: [waiting], nextCursor: "cursor-2" })
    .mockResolvedValueOnce({ items: [completed], nextCursor: null });
  const screen = await renderScreen(listImports);
  await waitFor(() => expect(screen.getByRole("button", { name: "Load more imports" })).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Load more imports" }));
  await waitFor(() => expect(screen.getByText("Complete")).toBeTruthy());
  expect(screen.getByText("Waiting")).toBeTruthy();
  expect(listImports.mock.calls[1][0]).toEqual(expect.objectContaining({ cursor: "cursor-2" }));
});

test("distinguishes offline, retryable, and unauthorized history failures", async () => {
  const listImports = jest.fn()
    .mockRejectedValueOnce(new ApiNetworkError({ code: "ERR_NETWORK" }))
    .mockRejectedValueOnce(new Error("provider payload"))
    .mockRejectedValueOnce(new ApiUnauthorizedError());
  const screen = await renderScreen(listImports);
  await waitFor(() => expect(screen.getByText("You’re offline. Check your connection and try again.")).toBeTruthy());
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.getByText("We couldn't load your imports. Please try again.")).toBeTruthy());
  expect(screen.queryByText("provider payload")).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(actions.onUnauthorized).toHaveBeenCalledTimes(1));
});
