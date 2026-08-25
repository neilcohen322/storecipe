import JSZip from "jszip";

import { ApiUnauthorizedError } from "../../api/client";
import type { CoverImageResponse, Recipe, RecipeQueryPage } from "../../api/catalog";
import type { ImportHistoryItem, ImportHistoryPage } from "../../api/ingestion";
import type { ExportCookbookZipArgs } from "../cookbookExport";
import { fixtureRecipe } from "../../testing/fixtures";
import {
  CookbookExportError,
  downloadBlob,
  exportCookbookZip,
  listAllImports,
  listAllRecipes,
  mapWithConcurrency,
} from "../cookbookExport";

const coverImage = {
  url: "/v1/recipes/recipe-with-cover/cover-image",
  etag: "a".repeat(64),
  byteSize: 8,
  contentType: "image/webp" as const,
};

const recipeWithCover: Recipe = {
  ...fixtureRecipe,
  id: "recipe-with-cover",
  coverImage,
  favorite: true,
  personalNotes: "Weeknight staple",
  lastCookedAt: "2026-08-20T12:00:00.000Z",
};

const recipeWithoutCover: Recipe = {
  ...fixtureRecipe,
  id: "recipe-without-cover",
  coverImage: null,
};

const importItem: ImportHistoryItem = {
  id: "import-1",
  inputKind: "url",
  createdAt: "2026-08-24T12:00:00.000Z",
  updatedAt: "2026-08-24T12:01:00.000Z",
  terminalAt: null,
  status: "processing",
  phase: "fetching",
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((next, fail) => {
    resolve = next;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function createCatalogMocks(options: {
  recipePages?: RecipeQueryPage[];
  coverResponses?: Record<string, CoverImageResponse | Error>;
}): ExportCookbookZipArgs["catalog"] {
  const listRecipes = jest.fn(async (params = {}) => {
    const pageIndex = params.cursor ? Number(params.cursor.replace("page-", "")) : 0;
    return options.recipePages?.[pageIndex] ?? { items: [], nextCursor: null };
  });
  const getCoverImage = jest.fn(async (recipeId: string) => {
    const response = options.coverResponses?.[recipeId];
    if (response instanceof Error) {
      throw response;
    }
    return response ?? { blob: new Blob(["cover"], { type: "image/webp" }), etag: "etag", notModified: false };
  });
  return { listRecipes, getCoverImage };
}

function createIngestionMocks(options: {
  importPages?: ImportHistoryPage[];
}): ExportCookbookZipArgs["ingestion"] {
  const listImports = jest.fn(async (params = {}) => {
    const pageIndex = params.cursor ? Number(params.cursor.replace("page-", "")) : 0;
    return options.importPages?.[pageIndex] ?? { items: [], nextCursor: null };
  });
  return { listImports };
}

const importJsZip = async (): Promise<typeof import("jszip")> =>
  ({ default: JSZip }) as unknown as typeof import("jszip");

beforeEach(() => {
  jest.clearAllMocks();
});

test("listAllRecipes and listAllImports paginate until nextCursor is null", async () => {
  const catalog = createCatalogMocks({
    recipePages: [
      { items: [recipeWithCover], nextCursor: "page-1" },
      { items: [recipeWithoutCover], nextCursor: null },
    ],
  });
  const ingestion = createIngestionMocks({
    importPages: [
      { items: [importItem], nextCursor: "page-1" },
      { items: [{ ...importItem, id: "import-2", phase: "completed", status: "completed" }], nextCursor: null },
    ],
  });

  await expect(listAllRecipes(catalog.listRecipes)).resolves.toEqual([recipeWithCover, recipeWithoutCover]);
  await expect(listAllImports(ingestion.listImports)).resolves.toHaveLength(2);
  expect(catalog.listRecipes).toHaveBeenNthCalledWith(1, { limit: 100, cursor: null });
  expect(catalog.listRecipes).toHaveBeenNthCalledWith(2, { limit: 100, cursor: "page-1" });
  expect(ingestion.listImports).toHaveBeenNthCalledWith(1, { limit: 100, cursor: null });
  expect(ingestion.listImports).toHaveBeenNthCalledWith(2, { limit: 100, cursor: "page-1" });
});

test("mapWithConcurrency never exceeds the requested concurrency", async () => {
  let inFlight = 0;
  let maxInFlight = 0;
  const items = Array.from({ length: 8 }, (_, index) => index);
  const gates = items.map(() => deferred<void>());

  const work = mapWithConcurrency(items, 4, async (_item, index) => {
    inFlight += 1;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await gates[index].promise;
    inFlight -= 1;
  });

  for (let index = 0; index < 4; index += 1) {
    gates[index].resolve();
  }
  await Promise.resolve();
  expect(maxInFlight).toBeLessThanOrEqual(4);

  for (let index = 4; index < gates.length; index += 1) {
    gates[index].resolve();
  }
  await work;
  expect(maxInFlight).toBeLessThanOrEqual(4);
});

test("exportCookbookZip preserves unauthorized errors instead of wrapping them", async () => {
  const catalog = createCatalogMocks({
    recipePages: [{ items: [recipeWithCover], nextCursor: null }],
  });
  catalog.listRecipes = jest.fn(async () => {
    throw new ApiUnauthorizedError();
  });
  const ingestion = createIngestionMocks({ importPages: [{ items: [], nextCursor: null }] });

  await expect(
    exportCookbookZip({
      catalog,
      ingestion,
      profile: { name: null, email: null },
      importZip: importJsZip,
    }),
  ).rejects.toBeInstanceOf(ApiUnauthorizedError);
});

test("exportCookbookZip preserves unauthorized cover fetches", async () => {
  const catalog = createCatalogMocks({
    recipePages: [{ items: [recipeWithCover], nextCursor: null }],
    coverResponses: {
      "recipe-with-cover": new ApiUnauthorizedError(),
    },
  });
  const ingestion = createIngestionMocks({ importPages: [{ items: [], nextCursor: null }] });

  await expect(
    exportCookbookZip({
      catalog,
      ingestion,
      profile: { name: null, email: null },
      importZip: importJsZip,
    }),
  ).rejects.toBeInstanceOf(ApiUnauthorizedError);
});

test("exportCookbookZip fails closed when an expected cover fetch fails", async () => {
  const catalog = createCatalogMocks({
    recipePages: [{ items: [recipeWithCover], nextCursor: null }],
    coverResponses: {
      "recipe-with-cover": new Error("network"),
    },
  });
  const ingestion = createIngestionMocks({ importPages: [{ items: [], nextCursor: null }] });

  await expect(
    exportCookbookZip({
      catalog,
      ingestion,
      profile: { name: "Ada", email: "ada@example.test" },
      now: () => new Date("2026-08-25T10:00:00.000Z"),
      importZip: importJsZip,
    }),
  ).rejects.toMatchObject({ code: "cover_fetch_failed" });
});

test("exportCookbookZip fails closed when an expected cover blob is empty", async () => {
  const catalog = createCatalogMocks({
    recipePages: [{ items: [recipeWithCover], nextCursor: null }],
    coverResponses: {
      "recipe-with-cover": { blob: new Blob([], { type: "image/webp" }), etag: "etag", notModified: false },
    },
  });
  const ingestion = createIngestionMocks({ importPages: [{ items: [], nextCursor: null }] });

  await expect(
    exportCookbookZip({
      catalog,
      ingestion,
      profile: { name: null, email: null },
      importZip: importJsZip,
    }),
  ).rejects.toMatchObject({ code: "cover_fetch_failed" });
});

test("recipes without coverImage succeed without a covers file", async () => {
  const catalog = createCatalogMocks({
    recipePages: [{ items: [recipeWithoutCover], nextCursor: null }],
  });
  const ingestion = createIngestionMocks({ importPages: [{ items: [], nextCursor: null }] });

  const result = await exportCookbookZip({
    catalog,
    ingestion,
    profile: { name: null, email: null },
    now: () => new Date("2026-08-25T10:00:00.000Z"),
    importZip: importJsZip,
  });

  const zip = await JSZip.loadAsync(await result.blob.arrayBuffer());
  const fileNames = Object.values(zip.files).filter((file) => !file.dir).map((file) => file.name);
  expect(fileNames).toEqual(["storecipe-export-v1.json"]);
});

test("exportCookbookZip writes schemaVersion 1, profile, recipes, and safe import fields", async () => {
  const catalog = createCatalogMocks({
    recipePages: [{ items: [recipeWithCover, recipeWithoutCover], nextCursor: null }],
  });
  const ingestion = createIngestionMocks({
    importPages: [{ items: [importItem], nextCursor: null }],
  });

  const result = await exportCookbookZip({
    catalog,
    ingestion,
    profile: { name: "Ada Lovelace", email: "ada@example.test" },
    now: () => new Date("2026-08-25T10:00:00.000Z"),
    importZip: importJsZip,
  });

  const zip = await JSZip.loadAsync(await result.blob.arrayBuffer());
  const payload = JSON.parse(await zip.file("storecipe-export-v1.json")!.async("string"));

  expect(payload).toEqual({
    schemaVersion: 1,
    exportedAt: "2026-08-25T10:00:00.000Z",
    profile: { name: "Ada Lovelace", email: "ada@example.test" },
    recipes: [recipeWithCover, recipeWithoutCover],
    imports: [{
      id: "import-1",
      inputKind: "url",
      createdAt: "2026-08-24T12:00:00.000Z",
      updatedAt: "2026-08-24T12:01:00.000Z",
      terminalAt: null,
      status: "processing",
      phase: "fetching",
    }],
  });
  expect(payload.imports[0]).not.toHaveProperty("createdRecipeId");
});

test("exportCookbookZip names the archive and cover files correctly", async () => {
  const catalog = createCatalogMocks({
    recipePages: [{ items: [recipeWithCover], nextCursor: null }],
  });
  const ingestion = createIngestionMocks({ importPages: [{ items: [], nextCursor: null }] });

  const result = await exportCookbookZip({
    catalog,
    ingestion,
    profile: { name: null, email: null },
    importZip: importJsZip,
  });

  expect(result.fileName).toBe("storecipe-export.zip");
  const zip = await JSZip.loadAsync(await result.blob.arrayBuffer());
  const fileNames = Object.values(zip.files).filter((file) => !file.dir).map((file) => file.name).sort();
  expect(fileNames).toEqual(["covers/recipe-with-cover.webp", "storecipe-export-v1.json"]);
});

test("downloadBlob triggers a browser download and revokes the object URL", () => {
  const createObjectURL = jest.fn(() => "blob:export");
  const revokeObjectURL = jest.fn();
  const click = jest.fn();
  const appendChild = jest.fn();
  const removeChild = jest.fn();
  const anchor = { href: "", download: "", style: { display: "" }, click };

  Object.defineProperty(globalThis, "URL", {
    configurable: true,
    value: { createObjectURL, revokeObjectURL },
  });
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: {
      createElement: jest.fn(() => anchor),
      body: { appendChild, removeChild },
    },
  });

  downloadBlob(new Blob(["zip"]), "storecipe-export.zip");

  expect(createObjectURL).toHaveBeenCalledTimes(1);
  expect(anchor.download).toBe("storecipe-export.zip");
  expect(click).toHaveBeenCalledTimes(1);
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:export");
});

test("downloadBlob fails visibly when browser download APIs are missing", () => {
  Object.defineProperty(globalThis, "URL", {
    configurable: true,
    value: {},
  });

  expect(() => downloadBlob(new Blob(["zip"]), "storecipe-export.zip")).toThrow(CookbookExportError);
});
