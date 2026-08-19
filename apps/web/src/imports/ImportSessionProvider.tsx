import { createContext, type PropsWithChildren, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, ApiUnauthorizedError } from "../api/client";
import type { ImportJob, createIngestionApi } from "../api/ingestion";
import { randomUuid } from "../utils/randomUuid";
import { canRetryImport, createImportPoller, type ImportPoller } from "../utils/importPolling";

export type ImportSource = { mode: "url" | "text"; value: string };
type NormalizedImportSource = ImportSource & { fingerprint: string };
type ImportAttempt = { source: NormalizedImportSource; key: string | null; jobId: string | null };
export type ImportTerminalSummary = {
  status: Extract<ImportJob["status"], "completed" | "review_required" | "failed" | "cancelled" | "timed_out">;
  canRetry: boolean;
  jobId: string;
  errorCategory: string | null;
  hasCandidate: boolean;
};

export type ImportSession = {
  activeJob: ImportJob | null;
  terminalSummary: ImportTerminalSummary | null;
  error: string | null;
  isStarting: boolean;
  startImport(source: ImportSource): Promise<void>;
  retryImport(): Promise<void>;
};

const ImportSessionContext = createContext<ImportSession | null>(null);

function normalizeSource(source: ImportSource): NormalizedImportSource {
  const value = source.value.trim();
  return { mode: source.mode, value, fingerprint: `${source.mode}:${value}` };
}

function startImportErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.errorCategory === "recipe_source_exists") {
      return "This URL is already saved in your library.";
    }
    if (error.errorCategory === "active_url_import_exists") {
      return "This URL is already being imported.";
    }
  }
  return "We couldn't start this import. Please try again.";
}

function queuedJob(jobId: string): ImportJob {
  return { id: jobId, status: "queued", attemptCount: 0, createdRecipeId: null, errorCategory: null, cancellationRequested: false, hasCandidate: false };
}

export function ImportSessionProvider({ children, ingestion, onUnauthorized }: PropsWithChildren<{ ingestion: ReturnType<typeof createIngestionApi>; onUnauthorized(): void }>) {
  const [activeJob, setActiveJob] = useState<ImportJob | null>(null);
  const [terminalSummary, setTerminalSummary] = useState<ImportTerminalSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isStarting, setIsStarting] = useState(false);
  const mountedRef = useRef(true);
  const startingRef = useRef(false);
  const attemptRef = useRef<ImportAttempt | null>(null);
  const ingestionRef = useRef(ingestion);
  const onUnauthorizedRef = useRef(onUnauthorized);
  const pollerRef = useRef<ImportPoller | null>(null);
  ingestionRef.current = ingestion;
  onUnauthorizedRef.current = onUnauthorized;

  if (pollerRef.current === null) {
    pollerRef.current = createImportPoller({
      getImport: (jobId) => ingestionRef.current.getImport(jobId),
      isActive: () => mountedRef.current,
      onJob: (job) => { setActiveJob(job); setError(null); },
      onTerminal: (job) => {
        const previous = attemptRef.current;
        if (previous) attemptRef.current = { ...previous, key: null, jobId: null };
        setActiveJob(null);
        setTerminalSummary({
          status: job.status as ImportTerminalSummary["status"],
          canRetry: canRetryImport(job),
          jobId: job.id,
          errorCategory: job.errorCategory,
          hasCandidate: job.hasCandidate,
        });
        setIsStarting(false);
      },
      onUnauthorized: () => onUnauthorizedRef.current(),
      onError: () => { setError("We couldn't check this import. Please try again."); setIsStarting(false); },
      isUnauthorizedError: (value) => value instanceof ApiUnauthorizedError,
    });
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; pollerRef.current?.stop(); };
  }, []);

  const startImport = useCallback(async (source: ImportSource) => {
    if (startingRef.current) return;
    const normalized = normalizeSource(source);
    const previous = attemptRef.current;
    const samePayload = previous?.source.fingerprint === normalized.fingerprint;
    const attempt: ImportAttempt = samePayload && previous
      ? previous
      : { source: normalized, key: randomUuid(), jobId: null };
    attemptRef.current = attempt;
    startingRef.current = true;
    setIsStarting(true);
    setError(null);
    setTerminalSummary(null);
    try {
      let jobId = attempt.jobId;
      if (jobId === null) {
        const key = attempt.key ?? randomUuid();
        attempt.key = key;
        const submission = attempt.source.mode === "url"
          ? await ingestionRef.current.createUrlImport(attempt.source.value, { idempotencyKey: key })
          : await ingestionRef.current.createTextImport(attempt.source.value, { idempotencyKey: key });
        jobId = submission.jobId;
        attempt.jobId = jobId;
      }
      if (!mountedRef.current) return;
      setActiveJob(queuedJob(jobId));
      pollerRef.current?.start(jobId);
    } catch (value) {
      if (!mountedRef.current) return;
      if (value instanceof ApiUnauthorizedError) {
        onUnauthorizedRef.current();
      } else {
        setError(startImportErrorMessage(value));
      }
    } finally {
      startingRef.current = false;
      if (mountedRef.current) setIsStarting(false);
    }
  }, []);

  const retryImport = useCallback(async () => {
    const previous = attemptRef.current;
    if (!previous || !terminalSummary?.canRetry) return;
    attemptRef.current = { ...previous, key: null, jobId: null };
    await startImport(previous.source);
  }, [startImport, terminalSummary]);

  const value = useMemo<ImportSession>(() => ({ activeJob, terminalSummary, error, isStarting, startImport, retryImport }), [activeJob, terminalSummary, error, isStarting, startImport, retryImport]);
  return <ImportSessionContext.Provider value={value}>{children}</ImportSessionContext.Provider>;
}

export function useImportSession(): ImportSession {
  const session = useContext(ImportSessionContext);
  if (session === null) throw new Error("useImportSession must be used within ImportSessionProvider");
  return session;
}
