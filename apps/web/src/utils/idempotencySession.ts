import { randomUuid } from "./randomUuid";

export type IdempotencySession = {
  key: string;
  fingerprint: string;
};

export function resolveIdempotencySession(
  previous: IdempotencySession | null,
  fingerprint: string,
  createKey: () => string = randomUuid,
): IdempotencySession {
  if (previous !== null && previous.fingerprint === fingerprint) {
    return previous;
  }
  return { key: createKey(), fingerprint };
}

/** Accepted import attempt retained across transient poll failures. */
export type ImportIdempotencyAttempt = {
  session: IdempotencySession;
  jobId: string | null;
};

export function resolveImportIdempotencyAttempt(
  previous: ImportIdempotencyAttempt | null,
  fingerprint: string,
  createKey: () => string = randomUuid,
): ImportIdempotencyAttempt {
  const session = resolveIdempotencySession(previous?.session ?? null, fingerprint, createKey);
  if (
    previous !== null &&
    previous.session.fingerprint === fingerprint &&
    previous.session.key === session.key
  ) {
    return { session, jobId: previous.jobId };
  }
  return { session, jobId: null };
}
