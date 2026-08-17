import type { ListRecipesParams, RecipeFacetSelectionsResponse, RecipeSort } from "../api/catalog";

export type RouteValue = string | string[] | undefined;
export type RouteQuery = Record<string, RouteValue>;

export const MAX_INGREDIENT_FILTERS = 32;
export const MAX_TAG_FILTERS = 16;

const SORTS: RecipeSort[] = [
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

function uniqueRouteStrings(value: RouteValue, maxItems: number): string[] {
  return [...new Set(routeStrings(value))].slice(0, maxItems);
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

export function normalizeRecipeListParams(route: RouteQuery): ListRecipesParams {
  const text = searchText(route.text);
  const ingredient = uniqueRouteStrings(route.ingredient, MAX_INGREDIENT_FILTERS);
  const tag = uniqueRouteStrings(route.tag, MAX_TAG_FILTERS);
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
  return {
    ...(text ? { text } : {}),
    ...(ingredient.length ? { ingredient } : {}),
    ...(tag.length ? { tag } : {}),
    ...(maxTotalMinutes !== null ? { maxTotalMinutes } : {}),
    ...(minRating !== null ? { minRating } : {}),
    ...(ratingState !== "any" ? { ratingState } : {}),
    sort: sort.length ? sort : DEFAULT_SORT,
    limit: 20,
  };
}

export function serializeRecipeListParams(params: ListRecipesParams): Record<string, string | string[]> {
  const { limit: _limit, cursor: _cursor, ...query } = params;
  const serialized: Record<string, string | string[]> = {};
  for (const key of [
    "text",
    "ingredient",
    "tag",
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
  const byRequested = new Map(items.map((item) => [item.requestedName, item]));
  const next = [...new Set(values.map((value) => {
    const item = byRequested.get(value);
    if (item?.status === "observed" && item.resolvedName !== null) return item.resolvedName;
    return value;
  }))];
  return next.length ? next : undefined;
}

export function applyCanonicalSelections(
  params: ListRecipesParams,
  resolution: RecipeFacetSelectionsResponse,
): ListRecipesParams {
  return {
    ...params,
    ingredient: canonicalizeBucket(params.ingredient, resolution.ingredients),
    tag: canonicalizeBucket(params.tag, resolution.tags),
  };
}
