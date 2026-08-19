import { ApiUnauthorizedError } from "../../api/client";
import {
  canRetryImport,
  createImportPoller,
  getImportPresentation,
} from "../importPolling";

test.each([
  ["queued", "waiting"],
  ["processing", "working"],
  ["completed", "complete"],
  ["review_required", "attention"],
  ["failed", "stopped"],
  ["cancelled", "stopped"],
  ["timed_out", "stopped"],
] as const)("maps %s to the total coarse presentation %s", (status, expected) => {
  expect(getImportPresentation({ status, errorCategory: null }).phase).toBe(expected);
});

test.each([
  [{ status: "failed", errorCategory: "timeout" }, true],
  [{ status: "failed", errorCategory: "connection_failure" }, true],
  [{ status: "failed", errorCategory: "dns_failure" }, true],
  [{ status: "failed", errorCategory: "rate_limited" }, true],
  [{ status: "failed", errorCategory: "provider_timeout" }, true],
  [{ status: "failed", errorCategory: "provider_rate_limited" }, true],
  [{ status: "failed", errorCategory: "provider_temporary" }, true],
  [{ status: "failed", errorCategory: "provider_transport" }, true],
  [{ status: "failed", errorCategory: "catalog_transport" }, true],
  [{ status: "timed_out", errorCategory: "import_deadline_exceeded" }, true],
  [{ status: "review_required", errorCategory: "provider_invalid_output" }, true],
  [{ status: "review_required", errorCategory: "daily_ai_budget_exceeded" }, true],
  [{ status: "failed", errorCategory: "validation_error" }, false],
  [{ status: "cancelled", errorCategory: null }, false],
  [{ status: "review_required", errorCategory: null }, false],
  [{ status: "review_required", errorCategory: "incomplete_extraction" }, false],
  [{ status: "completed", errorCategory: null }, false],
  [{ status: "failed", errorCategory: null }, false],
  [{ status: "failed", errorCategory: "unknown_future_value" }, false],
] as const)("allows retry only for safe terminal jobs", (job, expected) => {
  expect(canRetryImport(job)).toBe(expected);
});

function createFakeTimers() {
  let nextId = 1;
  const timers = new Map<number, () => void>();

  const setTimeoutFn = ((handler: TimerHandler, _delay?: number) => {
    const id = nextId;
    nextId += 1;
    timers.set(id, () => {
      if (typeof handler === "function") {
        handler();
      }
    });
    return id as unknown as ReturnType<typeof setTimeout>;
  }) as typeof setTimeout;

  const clearTimeoutFn = ((handle: ReturnType<typeof setTimeout>) => {
    timers.delete(Number(handle));
  }) as typeof clearTimeout;

  return {
    setTimeoutFn,
    clearTimeoutFn,
    pendingCount: () => timers.size,
    async flushNext() {
      const entry = timers.entries().next().value as
        | [number, () => void]
        | undefined;
      if (!entry) {
        throw new Error("No scheduled timer");
      }
      const [id, run] = entry;
      timers.delete(id);
      run();
      await Promise.resolve();
    },
  };
}

test("does not overlap polls while a request is in flight", async () => {
  const timers = createFakeTimers();
  let resolveImport: ((value: { id: string; status: "queued"; errorCategory: null; attemptCount: number; createdRecipeId: null; cancellationRequested: boolean; hasCandidate: boolean }) => void) | null =
    null;
  let calls = 0;

  const poller = createImportPoller({
    getImport: async () => {
      calls += 1;
      return await new Promise((resolve) => {
        resolveImport = resolve;
      });
    },
    isActive: () => true,
    onStatus: () => undefined,
    onTerminal: () => undefined,
    onUnauthorized: () => undefined,
    onError: () => undefined,
    isUnauthorizedError: () => false,
    intervalMs: 10,
    setTimeoutFn: timers.setTimeoutFn,
    clearTimeoutFn: timers.clearTimeoutFn,
  });

  poller.start("job-1");
  expect(calls).toBe(1);
  expect(timers.pendingCount()).toBe(0);

  resolveImport!({ id: "job-1", status: "queued", errorCategory: null, attemptCount: 0, createdRecipeId: null, cancellationRequested: false, hasCandidate: false });
  await new Promise<void>((resolve) => {
    queueMicrotask(() => queueMicrotask(resolve));
  });
  expect(calls).toBe(1);
  expect(timers.pendingCount()).toBe(1);

  await timers.flushNext();
  expect(calls).toBe(2);
  poller.stop();
});

test("stop during in-flight poll prevents status updates and further timers", async () => {
  const timers = createFakeTimers();
  let resolveImport: ((value: { id: string; status: "queued"; errorCategory: null; attemptCount: number; createdRecipeId: null; cancellationRequested: boolean; hasCandidate: boolean }) => void) | null =
    null;
  const statuses: string[] = [];

  const poller = createImportPoller({
    getImport: async () =>
      await new Promise((resolve) => {
        resolveImport = resolve;
      }),
    isActive: () => true,
    onStatus: (status) => statuses.push(status),
    onTerminal: () => undefined,
    onUnauthorized: () => undefined,
    onError: () => undefined,
    isUnauthorizedError: (error) => error instanceof ApiUnauthorizedError,
    intervalMs: 10,
    setTimeoutFn: timers.setTimeoutFn,
    clearTimeoutFn: timers.clearTimeoutFn,
  });

  poller.start("job-1");
  expect(statuses).toEqual(["queued"]);
  poller.stop();
  resolveImport!({ id: "job-1", status: "queued", errorCategory: null, attemptCount: 0, createdRecipeId: null, cancellationRequested: false, hasCandidate: false });
  await Promise.resolve();

  expect(statuses).toEqual(["queued"]);
  expect(timers.pendingCount()).toBe(0);
});

test("resumes polling the same job after a transient non-auth error", async () => {
  const timers = createFakeTimers();
  const jobIds: string[] = [];
  const errors: string[] = [];
  let calls = 0;
  const poller = createImportPoller({
    getImport: async (jobId) => {
      jobIds.push(jobId);
      calls += 1;
      if (calls === 1) {
        throw new Error("temporary transport failure");
      }
      return {
        id: jobId,
        status: "queued",
        errorCategory: null,
        attemptCount: 0,
        createdRecipeId: null,
        cancellationRequested: false,
        hasCandidate: false,
      };
    },
    isActive: () => true,
    onTerminal: () => undefined,
    onUnauthorized: () => undefined,
    onError: (message) => errors.push(message),
    isUnauthorizedError: (error) => error instanceof ApiUnauthorizedError,
    intervalMs: 10,
    setTimeoutFn: timers.setTimeoutFn,
    clearTimeoutFn: timers.clearTimeoutFn,
  });

  poller.start("job-1");
  await Promise.resolve();

  expect(errors).toEqual(["temporary transport failure"]);
  expect(timers.pendingCount()).toBe(1);

  await timers.flushNext();
  expect(jobIds).toEqual(["job-1", "job-1"]);
  poller.stop();
});

test("unauthorized poll errors invoke onUnauthorized once and stop", async () => {
  let unauthorizedCalls = 0;
  let terminalCalls = 0;
  const poller = createImportPoller({
    getImport: async () => {
      throw new ApiUnauthorizedError("expired");
    },
    isActive: () => true,
    onStatus: () => undefined,
    onTerminal: () => {
      terminalCalls += 1;
    },
    onUnauthorized: () => {
      unauthorizedCalls += 1;
    },
    onError: () => undefined,
    isUnauthorizedError: (error) => error instanceof ApiUnauthorizedError,
    intervalMs: 10,
  });

  poller.start("job-1");
  await Promise.resolve();

  expect(unauthorizedCalls).toBe(1);
  expect(terminalCalls).toBe(0);
});
