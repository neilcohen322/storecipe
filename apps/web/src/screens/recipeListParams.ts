import type { ListRecipesParams, RecipeFacetSelectionsResponse, RecipeSort } from "../api/catalog";

export type RouteValue = string | string[] | undefined;
export type RouteQuery = Record<string, RouteValue>;

const SORTS: RecipeSort[] = [
  "ingredientCoverage:asc",
  "ingredientCoverage:desc",
  "tagCoverage:asc",
  "tagCoverage:desc",
  "rating:asc",
  "rating:desc",
  "totalMinutes:asc",
  "totalMinutes:desc",
  "createdAt:asc",
  "createdAt:desc",
  "updatedAt:asc",
  "updatedAt:desc",
  "title:asc",
  "title:desc",
];
export const DEFAULT_SORT: RecipeSort[] = ["updatedAt:desc"];

export function routeStrings(value: RouteValue): string[] {
  const entries = Array.isArray(value) ? value : value === undefined ? [] : [value];
  return entries.filter((entry): entry is string => typeof entry === "string" && entry.length > 0);
}

function uniqueRouteStrings(value: RouteValue): string[] {
  return [...new Set(routeStrings(value))];
}

function searchText(value: RouteValue): string | null {
  const text = routeStrings(value)
    .flatMap((entry) => entry.split(","))
    .map((entry) => entry.trim().replace(/\s+/g, " "))
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
  return text || null;
}

function numberValue(value: RouteValue, minimum: number, maximum = Number.MAX_SAFE_INTEGER): number | null {
  const raw = Array.isArray(value) ? value[0] : value;
  const parsed = raw === undefined ? Number.NaN : Number(raw);
  return Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum ? parsed : null;
}

export function dropDependentSorts(params: ListRecipesParams): RecipeSort[] {
  const sort = params.sort ?? DEFAULT_SORT;
  const filtered = sort.filter((entry) => {
    const field = entry.split(":")[0];
    if (field === "ingredientCoverage" && !(params.availableIngredient?.length ?? 0)) return false;
    if (field === "tagCoverage" && !(params.preferredTag?.length ?? 0)) return false;
    return true;
  });
  return filtered.length ? filtered : DEFAULT_SORT;
}

export function normalizeRecipeListParams(route: RouteQuery): ListRecipesParams {
  const text = searchText(route.text);
  const requiredIngredient = uniqueRouteStrings(route.requiredIngredient);
  const availableIngredient = uniqueRouteStrings(route.availableIngredient);
  const requiredTag = uniqueRouteStrings(route.requiredTag);
  const preferredTag = uniqueRouteStrings(route.preferredTag);
  const seenSortFields = new Set<string>();
  const sort = routeStrings(route.sort)
    .filter((entry): entry is RecipeSort => (SORTS as string[]).includes(entry))
    .filter((entry) => {
      const field = entry.split(":")[0];
      if (seenSortFields.has(field)) return false;
      seenSortFields.add(field);
      return true;
    });
  const ratingState = route.ratingState === "rated" || route.ratingState === "unrated" ? route.ratingState : "any";
  const maxTotalMinutes = numberValue(route.maxTotalMinutes, 0);
  const minRating = ratingState === "unrated" ? null : numberValue(route.minRating, 1, 5);
  const normalized: ListRecipesParams = {
    ...(text ? { text } : {}),
    ...(requiredIngredient.length ? { requiredIngredient } : {}),
    ...(availableIngredient.length ? { availableIngredient } : {}),
    ...(requiredTag.length ? { requiredTag } : {}),
    ...(preferredTag.length ? { preferredTag } : {}),
    ...(maxTotalMinutes !== null ? { maxTotalMinutes } : {}),
    ...(minRating !== null ? { minRating } : {}),
    ...(ratingState !== "any" ? { ratingState } : {}),
    sort: sort.length ? sort : DEFAULT_SORT,
    limit: 20,
  };
  return { ...normalized, sort: dropDependentSorts(normalized) };
}

export function serializeRecipeListParams(params: ListRecipesParams): Record<string, string | string[]> {
  const { limit: _limit, cursor: _cursor, ...query } = params;
  const serialized: Record<string, string | string[]> = {};
  for (const key of [
    "text",
    "requiredIngredient",
    "availableIngredient",
    "requiredTag",
    "preferredTag",
    "maxTotalMinutes",
    "minRating",
    "ratingState",
    "sort",
  ] as const) {
    const value = query[key];
    if (
      value === undefined
      || value === null
      || value === ""
      || (Array.isArray(value) && value.length === 0)
      || (key === "ratingState" && value === "any")
      || (key === "sort" && Array.isArray(value) && value.join("|") === DEFAULT_SORT.join("|"))
    ) {
      continue;
    }
    serialized[key] = Array.isArray(value) ? value : String(value);
  }
  return serialized;
}

export function sameStringList(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function canonicalizeBucket(values: string[] | undefined, items: RecipeFacetSelectionsResponse["ingredients"]): string[] | undefined {
  if (!values?.length) return undefined;
  const byRequested = new Map(items.map((item) => [item.requestedName, item.normalizedName]));
  const next = [...new Set(values.map((value) => byRequested.get(value) ?? value))];
  return next.length ? next : undefined;
}

export function applyCanonicalSelections(
  params: ListRecipesParams,
  resolution: RecipeFacetSelectionsResponse,
): ListRecipesParams {
  return {
    ...params,
    requiredIngredient: canonicalizeBucket(params.requiredIngredient, resolution.ingredients),
    availableIngredient: canonicalizeBucket(params.availableIngredient, resolution.ingredients),
    requiredTag: canonicalizeBucket(params.requiredTag, resolution.tags),
    preferredTag: canonicalizeBucket(params.preferredTag, resolution.tags),
  };
}
