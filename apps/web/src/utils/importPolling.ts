import type { ImportJob, ImportJobStatus } from "../api/ingestion";

export type { ImportJobStatus } from "../api/ingestion";
export type ImportPollJob = Pick<ImportJob, "status" | "errorCategory">;
export type ImportPresentationPhase = "waiting" | "working" | "complete" | "attention" | "stopped";

export type ImportPresentation = {
  phase: ImportPresentationPhase;
  label: string;
};

const PRESENTATION_BY_STATUS: Record<ImportJobStatus, ImportPresentation> = {
  queued: { phase: "waiting", label: "Waiting to start" },
  processing: { phase: "working", label: "Import in progress" },
  completed: { phase: "complete", label: "Import complete" },
  review_required: { phase: "attention", label: "Review needed" },
  failed: { phase: "stopped", label: "Import stopped" },
  cancelled: { phase: "stopped", label: "Import stopped" },
  timed_out: { phase: "stopped", label: "Import stopped" },
};

const RETRYABLE_FAILED_CATEGORIES: ReadonlySet<string> = new Set([
  "timeout",
  "connection_failure",
  "dns_failure",
  "rate_limited",
  "provider_timeout",
  "provider_rate_limited",
  "provider_temporary",
  "provider_transport",
  "catalog_transport",
]);

export const TERMINAL_IMPORT_STATUSES: ReadonlySet<ImportJobStatus> = new Set([
  "completed",
  "review_required",
  "failed",
  "cancelled",
  "timed_out",
]);

export function getImportPresentation(job: ImportPollJob): ImportPresentation {
  return PRESENTATION_BY_STATUS[job.status];
}

export function canRetryImport(job: ImportPollJob): boolean {
  return (job.status === "failed" && job.errorCategory !== null && RETRYABLE_FAILED_CATEGORIES.has(job.errorCategory))
    || (job.status === "timed_out" && job.errorCategory === "import_deadline_exceeded");
}

/** @deprecated Screens should render getImportPresentation(job), never raw status details. */
export function formatImportStatus(job: ImportPollJob): string {
  return getImportPresentation(job).label;
}

export type ImportPoller = {
  start(jobId: string): void;
  stop(): void;
};

export type ImportPollerOptions = {
  getImport(jobId: string): Promise<ImportJob>;
  isActive(): boolean;
  onJob?(job: ImportJob): void;
  /** @deprecated Kept only for existing polling consumers. */
  onStatus?(statusText: string): void;
  onTerminal(job: ImportJob): void;
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
    options.onStatus?.("queued");
    const scheduleNext = () => {
      if (options.isActive() && currentGeneration === generation) {
        timer = setTimeoutFn(() => { void tick(); }, intervalMs);
      }
    };
    const tick = async () => {
      if (!options.isActive() || currentGeneration !== generation || inFlight) return;
      inFlight = true;
      clearTimer();
      let shouldContinue = false;
      try {
        const job = await options.getImport(jobId);
        if (!options.isActive() || currentGeneration !== generation) return;
        options.onJob?.(job);
        options.onStatus?.(formatImportStatus(job));
        if (TERMINAL_IMPORT_STATUSES.has(job.status)) {
          options.onTerminal(job);
          return;
        }
        shouldContinue = true;
      } catch (error) {
        if (!options.isActive() || currentGeneration !== generation) return;
        if (options.isUnauthorizedError(error)) {
          stop();
          options.onUnauthorized();
          return;
        }
        options.onError(error instanceof Error ? error.message : "Failed to poll import status");
      } finally {
        inFlight = false;
      }
      if (shouldContinue) scheduleNext();
    };
    void tick();
  };
  return { start, stop };
}
