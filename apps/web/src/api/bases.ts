import type { ApiClientBases } from "./client";

/** Metro only inlines static process.env.EXPO_PUBLIC_* property access. */
export function getApiBases(): ApiClientBases {
  const catalog =
    process.env.EXPO_PUBLIC_CATALOG_API_URL?.trim() || "http://localhost:8000";
  const ingestion =
    process.env.EXPO_PUBLIC_INGESTION_API_URL?.trim() || "http://localhost:8001";
  return { catalog, ingestion };
}
