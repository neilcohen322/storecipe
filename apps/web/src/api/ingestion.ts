import type { createApiClient } from "./client";
import { randomUuid } from "../utils/randomUuid";

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
  hasCandidate: boolean;
};

export type ImportAccepted = {
  jobId: string;
  status: "queued";
};

/** Normalized import submission result for polling via `getImport`. */
export type ImportSubmission = {
  jobId: string;
};

export type NormalizedIngredient = {
  rawText: string;
  name: string;
  canonicalName: string;
  quantity: number | null;
  unit: string | null;
};

export type IngredientNormalizationResponse = {
  ingredients: NormalizedIngredient[];
};

export type ImportReviewDraft = {
  title: string | null;
  sourceUrl: string | null;
  servings: number | null;
  prepMinutes: number | null;
  cookMinutes: number | null;
  totalMinutes: number | null;
  ingredients: string[];
  instructions: string[];
  tags: string[];
};

type ImportCreateOptions = {
  idempotencyKey?: string;
};

function resolveIdempotencyKey(idempotencyKey?: string): string {
  return idempotencyKey ?? randomUuid();
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

  const getImportDraft = (jobId: string): Promise<ImportReviewDraft> =>
    client.getJson<ImportReviewDraft>(`/v1/imports/${jobId}/draft`, {
      service: "ingestion",
    });

  const normalizeIngredients = async (
    ingredients: Array<{ rawText: string }>,
    idempotencyKey: string,
  ): Promise<IngredientNormalizationResponse> => {
    const response = await client.request("/v1/ingredient-normalizations", {
      service: "ingestion",
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify({ ingredients }),
    });
    return (await response.json()) as IngredientNormalizationResponse;
  };

  return { createUrlImport, createTextImport, getImport, getImportDraft, normalizeIngredients };
}
