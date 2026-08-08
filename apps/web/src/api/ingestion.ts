import type { createApiClient } from "./client";

export type ImportJobStatus =
  | "queued"
  | "processing"
  | "completed"
  | "review_required"
  | "failed"
  | "cancelled"
  | "timed_out";

export type ImportJob = {
  id: string;
  status: ImportJobStatus;
  attemptCount: number;
  createdRecipeId: string | null;
  errorCategory: string | null;
  cancellationRequested: boolean;
};

export type ImportAccepted = {
  jobId: string;
  status: "queued";
};

/** Normalized import submission result for polling via `getImport`. */
export type ImportSubmission = {
  jobId: string;
};

type ImportCreateOptions = {
  idempotencyKey?: string;
};

function resolveIdempotencyKey(idempotencyKey?: string): string {
  return idempotencyKey ?? crypto.randomUUID();
}

function normalizeImportSubmission(
  body: ImportAccepted | ImportJob,
): ImportSubmission {
  if ("jobId" in body) {
    return { jobId: body.jobId };
  }
  return { jobId: body.id };
}

export function createIngestionApi(client: ReturnType<typeof createApiClient>) {
  const createUrlImport = async (
    url: string,
    options: ImportCreateOptions = {},
  ): Promise<ImportSubmission> => {
    const response = await client.request("/v1/imports/url", {
      service: "ingestion",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": resolveIdempotencyKey(options.idempotencyKey),
      },
      body: JSON.stringify({ url }),
    });
    return normalizeImportSubmission(
      (await response.json()) as ImportAccepted | ImportJob,
    );
  };

  const createTextImport = async (
    text: string,
    options: ImportCreateOptions = {},
  ): Promise<ImportSubmission> => {
    const response = await client.request("/v1/imports/text", {
      service: "ingestion",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": resolveIdempotencyKey(options.idempotencyKey),
      },
      body: JSON.stringify({ text }),
    });
    return normalizeImportSubmission(
      (await response.json()) as ImportAccepted | ImportJob,
    );
  };

  const getImport = (jobId: string): Promise<ImportJob> =>
    client.getJson<ImportJob>(`/v1/imports/${jobId}`, {
      service: "ingestion",
    });

  return { createUrlImport, createTextImport, getImport };
}
