export type ImportJobStatus =
  | "queued"
  | "processing"
  | "completed"
  | "review_required"
  | "failed"
  | "cancelled"
  | "timed_out";

export type ImportPollJob = {
  status: ImportJobStatus;
  errorCategory: string | null;
};

export const TERMINAL_IMPORT_STATUSES: ReadonlySet<ImportJobStatus> = new Set([
  "completed",
  "review_required",
  "failed",
  "cancelled",
  "timed_out",
]);

export function formatImportStatus(
  job: Pick<ImportPollJob, "status" | "errorCategory">,
): string {
  if (job.errorCategory) {
    return `${job.status} (${job.errorCategory})`;
  }
  return job.status;
}

export type ImportPoller = {
  start(jobId: string): void;
  stop(): void;
};

export type ImportPollerOptions = {
  getImport(jobId: string): Promise<ImportPollJob>;
  isActive(): boolean;
  onStatus(statusText: string): void;
  onTerminal(): void;
  onUnauthorized(): void;
  onError(message: string): void;
  isUnauthorizedError(error: unknown): boolean;
  intervalMs?: number;
  setTimeoutFn?: typeof setTimeout;
  clearTimeoutFn?: typeof clearTimeout;
};

export function createImportPoller(options: ImportPollerOptions): ImportPoller {
  const intervalMs = options.intervalMs ?? 2000;
  const setTimeoutFn = options.setTimeoutFn ?? setTimeout;
  const clearTimeoutFn = options.clearTimeoutFn ?? clearTimeout;

  let generation = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let inFlight = false;

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeoutFn(timer);
      timer = null;
    }
  };

  const stop = () => {
    generation += 1;
    inFlight = false;
    clearTimer();
  };

  const start = (jobId: string) => {
    stop();
    const currentGeneration = generation;
    options.onStatus("queued");

    const scheduleNext = () => {
      if (!options.isActive() || currentGeneration !== generation) {
        return;
      }
      timer = setTimeoutFn(() => {
        void tick();
      }, intervalMs);
    };

    const tick = async () => {
      if (!options.isActive() || currentGeneration !== generation || inFlight) {
        return;
      }
      inFlight = true;
      clearTimer();
      let shouldContinue = false;
      try {
        const job = await options.getImport(jobId);
        if (!options.isActive() || currentGeneration !== generation) {
          return;
        }
        options.onStatus(formatImportStatus(job));
        if (TERMINAL_IMPORT_STATUSES.has(job.status)) {
          options.onTerminal();
          return;
        }
        shouldContinue = true;
      } catch (error) {
        if (!options.isActive() || currentGeneration !== generation) {
          return;
        }
        if (options.isUnauthorizedError(error)) {
          options.onUnauthorized();
          return;
        }
        options.onError(
          error instanceof Error ? error.message : "Failed to poll import status",
        );
      } finally {
        inFlight = false;
      }
      if (shouldContinue) {
        scheduleNext();
      }
    };

    void tick();
  };

  return { start, stop };
}
