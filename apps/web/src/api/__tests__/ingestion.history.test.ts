import { buildImportHistoryPath, createIngestionApi } from "../ingestion";
import type { createApiClient } from "../client";

test("buildImportHistoryPath omits empty cursor and serializes pagination", () => {
  expect(buildImportHistoryPath()).toBe("/v1/imports");
  expect(buildImportHistoryPath({})).toBe("/v1/imports");
  expect(buildImportHistoryPath({ cursor: null, limit: 20 })).toBe("/v1/imports?limit=20");
  expect(buildImportHistoryPath({ cursor: "abc", limit: 20 })).toBe("/v1/imports?cursor=abc&limit=20");
});

test("listImports and cancelImport use the collection and DELETE contracts", async () => {
  const getJson = jest.fn().mockResolvedValue({
    items: [{
      id: "job-1",
      inputKind: "url",
      createdAt: "2026-08-24T12:00:00.000Z",
      updatedAt: "2026-08-24T12:01:00.000Z",
      terminalAt: null,
      status: "processing",
      phase: "fetching",
    }],
    nextCursor: "cursor-2",
  });
  const request = jest.fn().mockResolvedValue({ status: 204 });
  const ingestion = createIngestionApi({ request, getJson } as unknown as ReturnType<typeof createApiClient>);

  const page = await ingestion.listImports({ limit: 20 });
  expect(getJson).toHaveBeenCalledWith("/v1/imports?limit=20", expect.objectContaining({ service: "ingestion" }));
  expect(page.items).toHaveLength(1);
  expect(page.nextCursor).toBe("cursor-2");

  await ingestion.cancelImport("job-1");
  expect(request).toHaveBeenCalledWith("/v1/imports/job-1", { service: "ingestion", method: "DELETE" });
});
