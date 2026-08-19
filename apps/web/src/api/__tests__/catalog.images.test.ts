import { createCatalogApi, parseCoverImage } from "../catalog";
import { ApiError, createApiClient } from "../client";

function jsonResponse(body: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

test("multipart cover upload does not set Content-Type", async () => {
  const seen: string[] = [];
  globalThis.fetch = (async (_input: RequestInfo, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    seen.push(headers.get("Content-Type") ?? "");
    expect(init?.body).toBeInstanceOf(FormData);
    return jsonResponse({
      url: "/v1/recipes/recipe-1/cover-image",
      etag: "a".repeat(64),
      byteSize: 12,
      contentType: "image/webp",
    });
  }) as typeof fetch;
  const catalog = createCatalogApi(createApiClient(async () => "token", { catalog: "http://catalog.test", ingestion: "http://ingestion.test" }));
  await catalog.uploadCoverImage("recipe-1", new Blob(["RIFF"], { type: "image/webp" }));
  expect(seen[0]).not.toMatch(/multipart/i);
  expect(seen[0]).toBe("");
});

test("GET without etag does not send If-None-Match", async () => {
  const seen: Array<string | null> = [];
  globalThis.fetch = (async (_input: RequestInfo, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    seen.push(headers.get("If-None-Match"));
    return new Response(new Blob(["RIFF"]), { status: 200, headers: { ETag: `"${"a".repeat(64)}"` } });
  }) as typeof fetch;
  const catalog = createCatalogApi(createApiClient(async () => "token", { catalog: "http://catalog.test", ingestion: "http://ingestion.test" }));
  await catalog.getCoverImage("recipe-1");
  expect(seen[0]).toBeNull();
});

test("binary GET forwards optional ETag and represents 304 without a blob", async () => {
  const seen: Array<string | null> = [];
  globalThis.fetch = (async (_input: RequestInfo, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    seen.push(headers.get("If-None-Match"));
    return new Response(null, { status: 304, headers: { ETag: `"${"b".repeat(64)}"` } });
  }) as typeof fetch;
  const catalog = createCatalogApi(createApiClient(async () => "token", { catalog: "http://catalog.test", ingestion: "http://ingestion.test" }));
  const result = await catalog.getCoverImage("recipe-1", { etag: "b".repeat(64) });
  expect(seen[0]).toBe(`"${"b".repeat(64)}"`);
  expect(result.blob).toBeNull();
  expect(result.notModified).toBe(true);
});

test("delete uses DELETE and recipe parsing retains coverImage", async () => {
  const methods: string[] = [];
  globalThis.fetch = (async (input: RequestInfo, init?: RequestInit) => {
    methods.push(init?.method ?? "GET");
    const url = String(input);
    if (url.includes("cover-image") && (init?.method ?? "GET") === "DELETE") {
      return new Response(null, { status: 204 });
    }
    return jsonResponse({
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
      coverImage: { url: "/v1/recipes/recipe-1/cover-image", etag: "c".repeat(64), byteSize: 8, contentType: "image/webp" },
    });
  }) as typeof fetch;
  const catalog = createCatalogApi(createApiClient(async () => "token", { catalog: "http://catalog.test", ingestion: "http://ingestion.test" }));
  await catalog.deleteCoverImage("recipe-1");
  const recipe = await catalog.getRecipe("recipe-1");
  expect(methods[0]).toBe("DELETE");
  expect(recipe.coverImage?.etag).toBe("c".repeat(64));
  expect(parseCoverImage(null)).toBeNull();
});

test("413 cover errors keep a safe category", async () => {
  globalThis.fetch = (async () =>
    new Response(JSON.stringify({ detail: "Choose an image smaller than 8 MB.", errorCategory: "image_too_large" }), {
      status: 413,
      headers: { "Content-Type": "application/problem+json" },
    })) as typeof fetch;
  const catalog = createCatalogApi(createApiClient(async () => "token", { catalog: "http://catalog.test", ingestion: "http://ingestion.test" }));
  await expect(catalog.uploadCoverImage("recipe-1", new Blob(["x"]))).rejects.toEqual(
    expect.objectContaining({ status: 413, errorCategory: "image_too_large" }) as ApiError,
  );
});
