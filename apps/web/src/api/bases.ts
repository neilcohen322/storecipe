import type { ApiClientBases } from "./client";

function validatedBase(value: string, label: string): string {
  const parsed = new URL(value);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error(`${label} must use HTTP or HTTPS`);
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(`${label} must not contain credentials, a query, or a fragment`);
  }
  return parsed.toString().replace(/\/$/, "");
}

export function resolveApiBases(
  catalogValue?: string,
  ingestionValue?: string,
): ApiClientBases {
  const catalog = validatedBase(
    catalogValue?.trim() || "http://localhost:8000",
    "EXPO_PUBLIC_CATALOG_API_URL",
  );
  const ingestion = validatedBase(
    ingestionValue?.trim() || "http://localhost:8001",
    "EXPO_PUBLIC_INGESTION_API_URL",
  );

  if ((catalog.startsWith("https://") || ingestion.startsWith("https://")) && catalog !== ingestion) {
    throw new Error("Production Catalog and Ingestion API bases must use one HTTPS origin");
  }
  return { catalog, ingestion };
}

/** Metro only inlines static process.env.EXPO_PUBLIC_* property access. */
export function getApiBases(): ApiClientBases {
  return resolveApiBases(
    process.env.EXPO_PUBLIC_CATALOG_API_URL,
    process.env.EXPO_PUBLIC_INGESTION_API_URL,
  );
}
