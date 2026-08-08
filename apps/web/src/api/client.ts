export type ApiService = "catalog" | "ingestion";

export type ApiClientBases = Record<ApiService, string>;

export type ApiRequestOptions = RequestInit & {
  service?: ApiService;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
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

function joinUrl(base: string, path: string): string {
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

async function errorDetail(response: Response): Promise<string | undefined> {
  if (!response.headers.get("Content-Type")?.includes("application/problem+json")) {
    return undefined;
  }

  try {
    const problem = (await response.json()) as { detail?: unknown };
    return typeof problem.detail === "string" ? problem.detail : undefined;
  } catch {
    return undefined;
  }
}

export function createApiClient(
  getAccessToken: () => Promise<string>,
  bases: ApiClientBases,
) {
  const request = async (
    path: string,
    { service = "catalog", headers: requestHeaders, ...init }: ApiRequestOptions = {},
  ): Promise<Response> => {
    const accessToken = await getAccessToken();
    const headers = new Headers(requestHeaders);
    headers.set("Authorization", `Bearer ${accessToken}`);

    const response = await fetch(joinUrl(bases[service], path), {
      ...init,
      headers,
    });

    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401) {
        throw new ApiUnauthorizedError(detail);
      }
      throw new ApiError(detail ?? `API request failed with status ${response.status}`, response.status);
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
