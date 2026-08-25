import { ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, Recipe } from "../api/catalog";
import type { createIngestionApi, ImportHistoryItem } from "../api/ingestion";

export type CookbookExportErrorCode = "cover_fetch_failed" | "export_failed" | "download_unsupported";

export class CookbookExportError extends Error {
  readonly code: CookbookExportErrorCode;

  constructor(message: string, code: CookbookExportErrorCode) {
    super(message);
    this.name = "CookbookExportError";
    this.code = code;
  }
}

export type CookbookExportProfile = {
  name: string | null;
  email: string | null;
};

export type CookbookExportPayload = {
  schemaVersion: 1;
  exportedAt: string;
  profile: CookbookExportProfile;
  recipes: Recipe[];
  imports: SafeImportHistoryItem[];
};

export type SafeImportHistoryItem = Pick<
  ImportHistoryItem,
  "id" | "inputKind" | "createdAt" | "updatedAt" | "terminalAt" | "status" | "phase"
>;

export type ExportCookbookZipArgs = {
  catalog: Pick<ReturnType<typeof createCatalogApi>, "listRecipes" | "getCoverImage">;
  ingestion: Pick<ReturnType<typeof createIngestionApi>, "listImports">;
  profile: CookbookExportProfile;
  now?: () => Date;
  importZip?: () => Promise<typeof import("jszip")>;
};

export type ExportCookbookZipResult = {
  blob: Blob;
  fileName: "storecipe-export.zip";
};

const PAGE_LIMIT = 100;
const COVER_FETCH_CONCURRENCY = 4;
const EXPORT_JSON_FILE = "storecipe-export-v1.json";
const EXPORT_ZIP_FILE = "storecipe-export.zip";

function toSafeImportItem(item: ImportHistoryItem): SafeImportHistoryItem {
  return {
    id: item.id,
    inputKind: item.inputKind,
    createdAt: item.createdAt,
    updatedAt: item.updatedAt,
    terminalAt: item.terminalAt,
    status: item.status,
    phase: item.phase,
  };
}

export async function listAllRecipes(
  listRecipes: ExportCookbookZipArgs["catalog"]["listRecipes"],
): Promise<Recipe[]> {
  const recipes: Recipe[] = [];
  let cursor: string | null = null;
  do {
    const page = await listRecipes({ limit: PAGE_LIMIT, cursor });
    recipes.push(...page.items);
    cursor = page.nextCursor;
  } while (cursor !== null);
  return recipes;
}

export async function listAllImports(
  listImports: ExportCookbookZipArgs["ingestion"]["listImports"],
): Promise<ImportHistoryItem[]> {
  const imports: ImportHistoryItem[] = [];
  let cursor: string | null = null;
  do {
    const page = await listImports({ limit: PAGE_LIMIT, cursor });
    imports.push(...page.items);
    cursor = page.nextCursor;
  } while (cursor !== null);
  return imports;
}

export async function mapWithConcurrency<T, R>(
  items: T[],
  concurrency: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (items.length === 0) {
    return [];
  }

  const results = new Array<R>(items.length);
  let nextIndex = 0;
  let failed = false;

  const worker = async () => {
    while (!failed) {
      const current = nextIndex;
      nextIndex += 1;
      if (current >= items.length) {
        return;
      }
      try {
        results[current] = await fn(items[current], current);
      } catch (error) {
        failed = true;
        throw error;
      }
    }
  };

  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, () => worker()),
  );
  return results;
}

async function fetchExpectedCovers(
  recipes: Recipe[],
  getCoverImage: ExportCookbookZipArgs["catalog"]["getCoverImage"],
): Promise<Map<string, Blob>> {
  const expected = recipes.filter((recipe): recipe is Recipe & { coverImage: NonNullable<Recipe["coverImage"]> } =>
    recipe.coverImage !== null,
  );
  const covers = new Map<string, Blob>();

  await mapWithConcurrency(expected, COVER_FETCH_CONCURRENCY, async (recipe) => {
    let response;
    try {
      response = await getCoverImage(recipe.id);
    } catch (error) {
      if (error instanceof ApiUnauthorizedError) {
        throw error;
      }
      throw new CookbookExportError(
        `Could not fetch cover image for recipe ${recipe.id}.`,
        "cover_fetch_failed",
      );
    }
    if (!response.blob || response.blob.size === 0) {
      throw new CookbookExportError(
        `Cover image for recipe ${recipe.id} was empty or unavailable.`,
        "cover_fetch_failed",
      );
    }
    covers.set(recipe.id, response.blob);
    return response.blob;
  });

  return covers;
}

export async function exportCookbookZip(args: ExportCookbookZipArgs): Promise<ExportCookbookZipResult> {
  const now = args.now ?? (() => new Date());
  const loadZip = args.importZip ?? (() => import("jszip"));

  try {
    const [recipes, imports] = await Promise.all([
      listAllRecipes(args.catalog.listRecipes),
      listAllImports(args.ingestion.listImports),
    ]);
    const covers = await fetchExpectedCovers(recipes, args.catalog.getCoverImage);

    const payload: CookbookExportPayload = {
      schemaVersion: 1,
      exportedAt: now().toISOString(),
      profile: args.profile,
      recipes,
      imports: imports.map(toSafeImportItem),
    };

    const jsZipModule = await loadZip();
    const JSZip = "default" in jsZipModule ? jsZipModule.default : jsZipModule;
    const zip = new JSZip();
    zip.file(EXPORT_JSON_FILE, JSON.stringify(payload, null, 2));
    for (const [recipeId, coverBlob] of covers) {
      zip.file(`covers/${recipeId}.webp`, await coverBlob.arrayBuffer());
    }

    const zipBytes = await zip.generateAsync({ type: "uint8array" });
    const blob = new Blob([new Uint8Array(zipBytes)], { type: "application/zip" });
    return { blob, fileName: EXPORT_ZIP_FILE };
  } catch (error) {
    if (error instanceof CookbookExportError || error instanceof ApiUnauthorizedError) {
      throw error;
    }
    throw new CookbookExportError("Cookbook export failed.", "export_failed");
  }
}

export function downloadBlob(blob: Blob, fileName: string): void {
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
    throw new CookbookExportError("Download is not supported in this environment.", "download_unsupported");
  }
  if (typeof document === "undefined") {
    throw new CookbookExportError("Download is not supported in this environment.", "download_unsupported");
  }

  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = fileName;
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    URL.revokeObjectURL(url);
  }
}
