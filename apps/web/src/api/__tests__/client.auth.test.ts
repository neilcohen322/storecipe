import { createApiClient } from "../client";

test("catalog request includes bearer from getAccessToken", async () => {
  const calls: { url: string; authorization: string | null }[] = [];
  const fetchMock = async (input: RequestInfo, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    calls.push({
      url: String(input),
      authorization: headers.get("Authorization"),
    });
    return new Response(JSON.stringify({ items: [], nextCursor: null }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  globalThis.fetch = fetchMock as typeof fetch;
  const client = createApiClient(async () => "test-api-token", {
    catalog: "http://catalog.test",
    ingestion: "http://ingestion.test",
  });
  await client.getJson("/v1/recipes");
  expect(calls[0]?.authorization).toBe("Bearer test-api-token");
});
