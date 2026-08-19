export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly errorCategory: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class ApiUnauthorizedError extends ApiError {
  constructor(message = "Authentication required") {
    super(message, 401);
    this.name = "ApiUnauthorizedError";
  }
}

export class ApiNetworkError extends Error {
  readonly category = "network";
  readonly code = "NETWORK_ERROR";

  constructor(cause: unknown) {
    super("Network request failed");
    this.name = "ApiNetworkError";
    Object.defineProperty(this, "cause", { value: cause });
  }
}

/** Credential states that require re-login; network/transient Auth0 failures stay retryable. */
const UNAUTHORIZED_CREDENTIAL_MARKERS = new Set([
  "NO_CREDENTIALS",
  "NO_REFRESH_TOKEN",
  "SESSION_EXPIRED",
  "INVALID_CREDENTIALS",
  "RENEW_FAILED",
  "login_required",
  "session_expired",
  "missing_refresh_token",
  "invalid_refresh_token",
  "invalid_grant",
  "consent_required",
  "mfa_required",
]);

export function isUnauthorizedCredentialError(error: unknown): boolean {
  if (!error || typeof error !== "object") {
    return false;
  }

  const candidate = error as {
    type?: unknown;
    code?: unknown;
    name?: unknown;
    error?: unknown;
  };

  for (const value of [candidate.type, candidate.code, candidate.name, candidate.error]) {
    if (typeof value !== "string" || value.length === 0) {
      continue;
    }
    if (UNAUTHORIZED_CREDENTIAL_MARKERS.has(value)) {
      return true;
    }
    if (UNAUTHORIZED_CREDENTIAL_MARKERS.has(value.toLowerCase())) {
      return true;
    }
  }

  return false;
}

export type ApiService = "catalog" | "ingestion";

export type ApiClientBases = Record<ApiService, string>;

export type ApiRequestOptions = RequestInit & {
  service?: ApiService;
  allowStatuses?: number[];
};

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

async function errorProblem(response: Response): Promise<{ detail?: string; errorCategory: string | null }> {
  if (!response.headers.get("Content-Type")?.includes("application/problem+json")) {
    return { errorCategory: null };
  }

  try {
    const problem = (await response.json()) as { detail?: unknown; errorCategory?: unknown };
    return {
      detail: typeof problem.detail === "string" ? problem.detail : undefined,
      errorCategory: typeof problem.errorCategory === "string" ? problem.errorCategory : null,
    };
  } catch {
    return { errorCategory: null };
  }
}

export function createApiClient(
  getAccessToken: () => Promise<string>,
  bases: ApiClientBases,
) {
  const request = async (
    path: string,
    options: ApiRequestOptions = {},
  ): Promise<Response> => {
    const { service = "catalog", headers: requestHeaders, allowStatuses = [], ...init } = options;
    let accessToken: string;
    try {
      accessToken = await getAccessToken();
    } catch (err) {
      if (isUnauthorizedCredentialError(err)) {
        const reason = err instanceof Error ? err.message : "Authentication required";
        throw new ApiUnauthorizedError(reason);
      }
      throw err instanceof Error ? err : new Error("Failed to obtain access token");
    }
    const headers = new Headers(requestHeaders);
    headers.set("Authorization", `Bearer ${accessToken}`);

    const url = joinUrl(bases[service], path);
    let response: Response;
    try {
      response = await fetch(url, {
        ...init,
        headers,
      });
    } catch (err) {
      throw new ApiNetworkError(err);
    }

    if (!response.ok && !allowStatuses.includes(response.status)) {
      const problem = await errorProblem(response);
      if (response.status === 401) {
        throw new ApiUnauthorizedError(problem.detail);
      }
      throw new ApiError(
        problem.detail ?? `API request failed with status ${response.status}`,
        response.status,
        problem.errorCategory,
      );
    }

    return response;
  };

  const getJson = async <T = unknown>(
    path: string,
    options: Omit<ApiRequestOptions, "method"> = {},
  ): Promise<T> => {
    const response = await request(path, { ...options, method: "GET" });
    return (await response.json()) as T;
  };

  return { request, getJson };
}
