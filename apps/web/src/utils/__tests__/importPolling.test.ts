import { ApiUnauthorizedError } from "../../api/client";
import { createImportPoller } from "../importPolling";

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
  let resolveImport: ((value: { status: "queued"; errorCategory: null }) => void) | null =
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

  resolveImport!({ status: "queued", errorCategory: null });
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
  let resolveImport: ((value: { status: "queued"; errorCategory: null }) => void) | null =
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
  resolveImport!({ status: "queued", errorCategory: null });
  await Promise.resolve();

  expect(statuses).toEqual(["queued"]);
  expect(timers.pendingCount()).toBe(0);
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
