import { act, render, waitFor } from "@testing-library/react-native";
import { Text } from "react-native";

import type { createIngestionApi } from "../../api/ingestion";
import {
  ImportSessionProvider,
  useImportSession,
} from "../ImportSessionProvider";

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((next, fail) => {
    resolve = next;
    reject = fail;
  });
  return { promise, resolve, reject };
};

function SessionProbe({ onReady }: { onReady(session: ReturnType<typeof useImportSession>): void }) {
  const session = useImportSession();
  onReady(session);
  return <Text>{session.activeJob?.id ?? "no-active-job"}</Text>;
}

function ingestionWith(overrides: Partial<ReturnType<typeof createIngestionApi>> = {}) {
  return {
    createUrlImport: jest.fn().mockResolvedValue({ jobId: "job-1" }),
    createTextImport: jest.fn().mockResolvedValue({ jobId: "job-1" }),
    getImport: jest.fn().mockResolvedValue({ id: "job-1", status: "queued", attemptCount: 0, createdRecipeId: null, errorCategory: null, cancellationRequested: false }),
    ...overrides,
  } as unknown as ReturnType<typeof createIngestionApi>;
}

test("keeps the accepted job and idempotency key when polling has a transient failure", async () => {
  const getImport = jest.fn().mockRejectedValueOnce(new Error("transport details")).mockResolvedValue({ id: "job-1", status: "queued", attemptCount: 0, createdRecipeId: null, errorCategory: null, cancellationRequested: false });
  const ingestion = ingestionWith({ getImport });
  let session!: ReturnType<typeof useImportSession>;
  await render(<ImportSessionProvider ingestion={ingestion} onUnauthorized={jest.fn()}><SessionProbe onReady={(next) => { session = next; }} /></ImportSessionProvider>);

  await act(async () => { await session.startImport({ mode: "url", value: " https://example.com/soup " }); });
  await waitFor(() => expect(session.error).toBe("We couldn't check this import. Please try again."));
  await act(async () => { await session.startImport({ mode: "url", value: "https://example.com/soup" }); });

  expect(ingestion.createUrlImport).toHaveBeenCalledTimes(1);
  expect(getImport).toHaveBeenCalledWith("job-1");
  expect(getImport).toHaveBeenCalledTimes(2);
  expect((ingestion.createUrlImport as jest.Mock).mock.calls[0]?.[1]?.idempotencyKey).toBeDefined();
});

test("rotates to a new idempotency session when normalized payload changes", async () => {
  const first = deferred<{ jobId: string }>();
  const ingestion = ingestionWith({ createTextImport: jest.fn(() => first.promise) });
  let session!: ReturnType<typeof useImportSession>;
  await render(<ImportSessionProvider ingestion={ingestion} onUnauthorized={jest.fn()}><SessionProbe onReady={(next) => { session = next; }} /></ImportSessionProvider>);

  let starting!: Promise<void>;
  await act(async () => { starting = session.startImport({ mode: "text", value: "soup" }); });
  await waitFor(() => expect(ingestion.createTextImport).toHaveBeenCalledTimes(1));
  await act(async () => { first.reject(new Error("network")); await first.promise.catch(() => undefined); });
  await starting;
  await waitFor(() => expect(session.error).toBe("We couldn't start this import. Please try again."));
  await act(async () => { await session.startImport({ mode: "text", value: "stew" }); });

  expect(ingestion.createTextImport).toHaveBeenCalledTimes(2);
  expect((ingestion.createTextImport as jest.Mock).mock.calls[1]?.[1]?.idempotencyKey).not.toBe((ingestion.createTextImport as jest.Mock).mock.calls[0]?.[1]?.idempotencyKey);
});

test("clears the active job at terminal status but retains a safe retryable summary", async () => {
  const ingestion = ingestionWith({ getImport: jest.fn().mockResolvedValue({ id: "job-1", status: "failed", attemptCount: 1, createdRecipeId: null, errorCategory: "provider_timeout", cancellationRequested: false }) });
  let session!: ReturnType<typeof useImportSession>;
  await render(<ImportSessionProvider ingestion={ingestion} onUnauthorized={jest.fn()}><SessionProbe onReady={(next) => { session = next; }} /></ImportSessionProvider>);

  await act(async () => { await session.startImport({ mode: "text", value: "  soup  " }); });
  await waitFor(() => expect(session.activeJob).toBeNull());

  expect(session.terminalSummary).toEqual({ status: "failed", canRetry: true });
  await act(async () => { await session.retryImport(); });
  expect(ingestion.createTextImport).toHaveBeenCalledTimes(2);
  expect((ingestion.createTextImport as jest.Mock).mock.calls[1]?.[1]?.idempotencyKey).not.toBe((ingestion.createTextImport as jest.Mock).mock.calls[0]?.[1]?.idempotencyKey);
});
