import {
  ApiNetworkError,
  ApiUnauthorizedError,
  createApiClient,
  isUnauthorizedCredentialError,
} from "../client";
import { createCatalogApi } from "../catalog";
import { createIngestionApi } from "../ingestion";

function mockOkJson(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

test("catalog request includes bearer from getAccessToken", async () => {
  const calls: { url: string; authorization: string | null }[] = [];
  const fetchMock = async (input: RequestInfo, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    calls.push({
      url: String(input),
      authorization: headers.get("Authorization"),
    });
    return mockOkJson({ items: [], nextCursor: null });
  };
  globalThis.fetch = fetchMock as typeof fetch;
  const client = createApiClient(async () => "test-api-token", {
    catalog: "http://catalog.test",
    ingestion: "http://ingestion.test",
  });
  await client.getJson("/v1/recipes");
  expect(calls[0]?.authorization).toBe("Bearer test-api-token");
});

test("ingestion request includes bearer from getAccessToken", async () => {
  const calls: { url: string; authorization: string | null }[] = [];
  globalThis.fetch = (async (input: RequestInfo, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    calls.push({
      url: String(input),
      authorization: headers.get("Authorization"),
    });
    return mockOkJson({
      id: "job-1",
      status: "queued",
      attemptCount: 0,
      createdRecipeId: null,
      errorCategory: null,
      cancellationRequested: false,
      hasCandidate: false,
    });
  }) as typeof fetch;

  const client = createApiClient(async () => "ingestion-token", {
    catalog: "http://catalog.test",
    ingestion: "http://ingestion.test",
  });
  const ingestion = createIngestionApi(client);
  await ingestion.getImport("job-1");

  expect(calls[0]?.url).toBe("http://ingestion.test/v1/imports/job-1");
  expect(calls[0]?.authorization).toBe("Bearer ingestion-token");
});

test("login_required credential errors become ApiUnauthorizedError", async () => {
  const client = createApiClient(async () => {
    const error = new Error("Login required") as Error & { code: string; type: string };
    error.code = "login_required";
    error.type = "NO_CREDENTIALS";
    throw error;
  }, {
    catalog: "http://catalog.test",
    ingestion: "http://ingestion.test",
  });

  await expect(client.getJson("/v1/recipes")).rejects.toBeInstanceOf(ApiUnauthorizedError);
});

test("transient Auth0/network token failures are not unauthorized", async () => {
  const networkError = new Error("network down") as Error & { type: string; code: string };
  networkError.type = "NO_NETWORK";
  networkError.code = "temporarily_unavailable";

  expect(isUnauthorizedCredentialError(networkError)).toBe(false);

  const client = createApiClient(async () => {
    throw networkError;
  }, {
    catalog: "http://catalog.test",
    ingestion: "http://ingestion.test",
  });

  await expect(client.getJson("/v1/recipes")).rejects.toBe(networkError);
});

test("wraps fetch transport failures in the closed ApiNetworkError type", async () => {
  const transportFailure = Object.assign(new TypeError("fetch failed"), { code: "ERR_NETWORK" });
  globalThis.fetch = jest.fn().mockRejectedValue(transportFailure) as unknown as typeof fetch;
  const client = createApiClient(async () => "token", {
    catalog: "http://catalog.test",
    ingestion: "http://ingestion.test",
  });

  await expect(client.getJson("/v1/recipes")).rejects.toBeInstanceOf(ApiNetworkError);
});

test("createRecipe reuses caller-supplied idempotency key", async () => {
  const keys: string[] = [];
  globalThis.fetch = (async (_input: RequestInfo, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    keys.push(headers.get("Idempotency-Key") ?? "");
    return mockOkJson({
      id: "recipe-1",
      title: "Soup",
      sourceUrl: null,
      servings: null,
      prepMinutes: null,
      cookMinutes: null,
      totalMinutes: null,
      ingredients: [],
      instructions: [],
      tags: [],
      rating: null,
    });
  }) as typeof fetch;

  const client = createApiClient(async () => "token", {
    catalog: "http://catalog.test",
    ingestion: "http://ingestion.test",
  });
  const catalog = createCatalogApi(client);
  await catalog.createRecipe(
    { title: "Soup", ingredients: [], instructions: [] },
    "stable-key",
  );
  await catalog.createRecipe(
    { title: "Soup", ingredients: [], instructions: [] },
    "stable-key",
  );

  expect(keys).toEqual(["stable-key", "stable-key"]);
});

test("url import keeps an explicit idempotency key across calls", async () => {
  const keys: string[] = [];
  globalThis.fetch = (async (_input: RequestInfo, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    keys.push(headers.get("Idempotency-Key") ?? "");
    return new Response(JSON.stringify({ jobId: "job-1", status: "queued" }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    });
  }) as typeof fetch;

  const client = createApiClient(async () => "token", {
    catalog: "http://catalog.test",
    ingestion: "http://ingestion.test",
  });
  const ingestion = createIngestionApi(client);
  await ingestion.createUrlImport("https://example.com/a", {
    idempotencyKey: "import-key",
  });
  await ingestion.createUrlImport("https://example.com/a", {
    idempotencyKey: "import-key",
  });

  expect(keys).toEqual(["import-key", "import-key"]);
});

test("normalizeIngredients posts reviewed raw lines with bearer and idempotency key", async () => {
  const calls: {
    url: string;
    authorization: string | null;
    idempotencyKey: string | null;
    body: string | null;
  }[] = [];
  globalThis.fetch = (async (input: RequestInfo, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    calls.push({
      url: String(input),
      authorization: headers.get("Authorization"),
      idempotencyKey: headers.get("Idempotency-Key"),
      body: typeof init?.body === "string" ? init.body : null,
    });
    return mockOkJson({
      ingredients: [
        {
          rawText: "2 cups flour",
          name: "flour",
          canonicalName: "flour",
          quantity: 2,
          unit: "cups",
        },
      ],
    });
  }) as typeof fetch;

  const client = createApiClient(async () => "ingestion-token", {
    catalog: "http://catalog.test",
    ingestion: "http://ingestion.test",
  });
  const ingestion = createIngestionApi(client);
  await ingestion.normalizeIngredients([{ rawText: "2 cups flour" }], "normalize-key");

  expect(calls[0]?.url).toBe("http://ingestion.test/v1/ingredient-normalizations");
  expect(calls[0]?.authorization).toBe("Bearer ingestion-token");
  expect(calls[0]?.idempotencyKey).toBe("normalize-key");
  expect(calls[0]?.body).toBe(JSON.stringify({ ingredients: [{ rawText: "2 cups flour" }] }));
});
