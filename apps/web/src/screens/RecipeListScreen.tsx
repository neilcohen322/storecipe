import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";
import { TextInput, View } from "react-native";

import { ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, ListRecipesParams, RecipeQueryItem, RecipeSort } from "../api/catalog";
import { RecipeCard } from "../components/RecipeCard";
import { Button, EmptyState, ErrorState, OfflineBanner, PageHeader, ResponsiveGrid, Screen, Skeleton } from "../components";

type RouteValue = string | string[] | undefined;
type RouteQuery = Record<string, RouteValue>;
type ErrorState = "none" | "offline" | "generic";
const SORTS: RecipeSort[] = ["ingredientCoverage:asc", "ingredientCoverage:desc", "tagCoverage:asc", "tagCoverage:desc", "rating:asc", "rating:desc", "totalMinutes:asc", "totalMinutes:desc", "createdAt:asc", "createdAt:desc", "updatedAt:asc", "updatedAt:desc", "title:asc", "title:desc"];
const DEFAULT_SORT: RecipeSort[] = ["updatedAt:desc"];

function strings(value: RouteValue): string[] { return (Array.isArray(value) ? value : value ? [value] : []).flatMap((entry) => entry.split(",")).map((entry) => entry.trim().replace(/\s+/g, " ")).filter(Boolean); }
function normalizedSet(value: RouteValue): string[] { return [...new Set(strings(value).map((entry) => entry.toLocaleLowerCase()))].sort((a, b) => a.localeCompare(b)); }
function numberValue(value: RouteValue, minimum: number, maximum = Number.MAX_SAFE_INTEGER): number | null { const raw = Array.isArray(value) ? value[0] : value; const parsed = raw === undefined ? Number.NaN : Number(raw); return Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum ? parsed : null; }
function isAbortError(error: unknown): boolean { return error instanceof Error && error.name === "AbortError"; }
export function isOfflineError(error: unknown): boolean {
  if (error instanceof ApiNetworkError) return true;
  if (!error || typeof error !== "object") return false;
  const candidate = error as { name?: unknown; code?: unknown; cause?: unknown };
  if (candidate.code === "ERR_NETWORK" || candidate.code === "NETWORK_ERROR") return true;
  if (candidate.name === "TypeError" && candidate.cause && typeof candidate.cause === "object") {
    const cause = candidate.cause as { code?: unknown };
    return cause.code === "ERR_NETWORK" || cause.code === "NETWORK_ERROR";
  }
  return false;
}

export function createPaginationRequestGuard() {
  let activeRequestId: number | null = null;
  return {
    isActive: () => activeRequestId !== null,
    start: (requestId: number) => { activeRequestId = requestId; },
    finish: (requestId: number) => { if (activeRequestId === requestId) activeRequestId = null; },
    reset: () => { activeRequestId = null; },
  };
}

export function normalizeRecipeListParams(route: RouteQuery): ListRecipesParams {
  const text = strings(route.text).join(" ").toLocaleLowerCase() || null;
  const requiredIngredient = normalizedSet(route.requiredIngredient); const availableIngredient = normalizedSet(route.availableIngredient);
  const requiredTag = normalizedSet(route.requiredTag); const preferredTag = normalizedSet(route.preferredTag);
  const seenSortFields = new Set<string>();
  const sort = strings(route.sort).filter((entry): entry is RecipeSort => (SORTS as string[]).includes(entry)).filter((entry) => { const field = entry.split(":")[0]; if (seenSortFields.has(field)) return false; seenSortFields.add(field); return true; }).filter((entry) => entry.split(":")[0] !== "ingredientCoverage" || availableIngredient.length > 0).filter((entry) => entry.split(":")[0] !== "tagCoverage" || preferredTag.length > 0);
  const ratingState = route.ratingState === "rated" || route.ratingState === "unrated" ? route.ratingState : "any";
  const maxTotalMinutes = numberValue(route.maxTotalMinutes, 0); const minRating = ratingState === "unrated" ? null : numberValue(route.minRating, 1, 5);
  return { ...(text ? { text } : {}), ...(requiredIngredient.length ? { requiredIngredient } : {}), ...(availableIngredient.length ? { availableIngredient } : {}), ...(requiredTag.length ? { requiredTag } : {}), ...(preferredTag.length ? { preferredTag } : {}), ...(maxTotalMinutes !== null ? { maxTotalMinutes } : {}), ...(minRating !== null ? { minRating } : {}), ...(ratingState !== "any" ? { ratingState } : {}), sort: sort.length ? sort : DEFAULT_SORT, limit: 20 };
}

export function serializeRecipeListParams(params: ListRecipesParams): Record<string, string | string[]> {
  const { limit: _limit, cursor: _cursor, ...query } = params; const serialized: Record<string, string | string[]> = {};
  for (const key of ["text", "requiredIngredient", "availableIngredient", "requiredTag", "preferredTag", "maxTotalMinutes", "minRating", "ratingState", "sort"] as const) {
    const value = query[key]; if (value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0) || (key === "ratingState" && value === "any") || (key === "sort" && Array.isArray(value) && value.join("|") === DEFAULT_SORT.join("|"))) continue;
    serialized[key] = Array.isArray(value) ? value : String(value);
  }
  return serialized;
}

export type RecipeListScreenProps = { catalog: ReturnType<typeof createCatalogApi>; onOpenDetail(recipeId: string): void; onCreate(): void; onImport(): void; onLogout(): void; onUnauthorized(): void; };

export function RecipeListScreen({ catalog, onOpenDetail, onCreate, onImport, onLogout, onUnauthorized }: RecipeListScreenProps) {
  const router = useRouter(); const route = useLocalSearchParams() as RouteQuery; const routeKey = JSON.stringify(route);
  const params = useMemo(() => normalizeRecipeListParams(route), [routeKey]); const queryKey = JSON.stringify(serializeRecipeListParams(params));
  const routeSearchText = params.text ?? ""; const [items, setItems] = useState<RecipeQueryItem[]>([]); const [nextCursor, setNextCursor] = useState<string | null>(null); const [loading, setLoading] = useState(true); const [loadingMore, setLoadingMore] = useState(false); const [error, setError] = useState<ErrorState>("none"); const [view, setView] = useState<"card" | "list">("card"); const [searchDraft, setSearchDraft] = useState(routeSearchText);
  const mounted = useRef(true); const requestId = useRef(0); const debounce = useRef<ReturnType<typeof setTimeout> | null>(null); const draftGeneration = useRef(0); const controller = useRef<AbortController | null>(null); const paginationGuard = useRef(createPaginationRequestGuard());
  const previousRouteSearchText = useRef(routeSearchText);
  if (previousRouteSearchText.current !== routeSearchText) { previousRouteSearchText.current = routeSearchText; draftGeneration.current += 1; setSearchDraft(routeSearchText); }
  void onCreate; void onImport; void onLogout;
  const navigate = useCallback((next: ListRecipesParams) => router.push({ pathname: "/recipes", params: serializeRecipeListParams(next) }), [router]);
  const request = useCallback(async (cursor: string | null = null) => {
    const pagination = cursor !== null;
    if (pagination && paginationGuard.current.isActive()) return;
    const id = ++requestId.current;
    if (!pagination) controller.current?.abort();
    const nextController = new AbortController(); if (!pagination) controller.current = nextController;
    if (pagination) { paginationGuard.current.start(id); setLoadingMore(true); } else { setError("none"); }
    try {
      const page = await catalog.listRecipes({ ...params, ...(cursor ? { cursor } : {}) }, { signal: nextController.signal });
      if (!mounted.current || id !== requestId.current) return;
      setItems((current) => pagination ? [...new Map([...current, ...page.items].map((item) => [item.recipe.id, item])).values()] : page.items);
      setNextCursor(page.nextCursor);
    } catch (caught) {
      if (!mounted.current || id !== requestId.current || isAbortError(caught)) return;
      if (caught instanceof ApiUnauthorizedError) onUnauthorized(); else setError(isOfflineError(caught) ? "offline" : "generic");
    } finally {
      if (pagination) paginationGuard.current.finish(id);
      if (mounted.current && id === requestId.current) { setLoading(false); setLoadingMore(false); }
    }
  }, [catalog, onUnauthorized, params]);
  useEffect(() => { mounted.current = true; void request(); return () => { mounted.current = false; controller.current?.abort(); requestId.current += 1; paginationGuard.current.reset(); }; }, [queryKey, request]);
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current); }, []);
  const scheduleSearch = (text: string) => { setSearchDraft(text); if (debounce.current) clearTimeout(debounce.current); const generation = ++draftGeneration.current; debounce.current = setTimeout(() => { if (draftGeneration.current === generation) navigate(normalizeRecipeListParams({ ...serializeRecipeListParams(params), text })); }, 300); };
  const update = (key: keyof ListRecipesParams, value: string) => navigate(normalizeRecipeListParams({ ...serializeRecipeListParams(params), [key]: value }));
  const errorContent = error === "offline" ? <><OfflineBanner message="You’re offline. Check your connection and try again." /><Button label="Try again" onPress={() => void request()} /></> : <ErrorState title="We couldn't load your recipes. Please try again." action={<Button label="Try again" onPress={() => void request()} />} />;
  return <Screen><PageHeader title="Recipes" subtitle={items.length ? `${items.length} recipes loaded` : undefined} />
    <TextInput accessibilityLabel="Search recipes" value={searchDraft} onChangeText={scheduleSearch} placeholder="Search recipes" />
    <TextInput accessibilityLabel="Required ingredients" value={params.requiredIngredient?.join(", ") ?? ""} onEndEditing={(event) => update("requiredIngredient", event.nativeEvent.text)} placeholder="Required ingredients" />
    <TextInput accessibilityLabel="Available ingredients" value={params.availableIngredient?.join(", ") ?? ""} onEndEditing={(event) => update("availableIngredient", event.nativeEvent.text)} placeholder="Available ingredients" />
    <TextInput accessibilityLabel="Required tags" value={params.requiredTag?.join(", ") ?? ""} onEndEditing={(event) => update("requiredTag", event.nativeEvent.text)} placeholder="Required tags" />
    <TextInput accessibilityLabel="Preferred tags" value={params.preferredTag?.join(", ") ?? ""} onEndEditing={(event) => update("preferredTag", event.nativeEvent.text)} placeholder="Preferred tags" />
    <TextInput accessibilityLabel="Maximum total minutes" value={params.maxTotalMinutes?.toString() ?? ""} keyboardType="numeric" onEndEditing={(event) => update("maxTotalMinutes", event.nativeEvent.text)} placeholder="Maximum total minutes" />
    <TextInput accessibilityLabel="Minimum rating" value={params.minRating?.toString() ?? ""} keyboardType="numeric" onEndEditing={(event) => update("minRating", event.nativeEvent.text)} placeholder="Minimum rating" />
    <TextInput accessibilityLabel="Sort order" value={params.sort?.join(", ") ?? ""} onEndEditing={(event) => update("sort", event.nativeEvent.text)} placeholder="Sort order" />
    <View style={{ flexDirection: "row", gap: 8, marginVertical: 12 }}>{(["any", "rated", "unrated"] as const).map((state) => <Button key={state} label={state === "any" ? "Any rating" : state === "rated" ? "Rated only" : "Unrated only"} variant={(params.ratingState ?? "any") === state ? "primary" : "secondary"} accessibilityState={{ selected: (params.ratingState ?? "any") === state }} onPress={() => navigate(normalizeRecipeListParams({ ...serializeRecipeListParams(params), ratingState: state }))} />)}</View>
    <View style={{ flexDirection: "row", gap: 8, marginBottom: 16 }}><Button label="Card view" variant={view === "card" ? "primary" : "secondary"} onPress={() => setView("card")} /><Button label="List view" variant={view === "list" ? "primary" : "secondary"} onPress={() => setView("list")} /></View>
    {loading && items.length === 0 ? <ResponsiveGrid>{[0, 1, 2].map((slot) => <View key={slot} testID="recipe-card-skeleton"><Skeleton height={220} /></View>)}</ResponsiveGrid> : error !== "none" && items.length === 0 ? errorContent : items.length === 0 ? <EmptyState title="Your recipe library is empty." description="Create or import a recipe to start building your library." /> : view === "card" ? <ResponsiveGrid testID="recipe-results-card">{items.map((item) => <RecipeCard key={item.recipe.id} item={item} onOpen={onOpenDetail} view="card" />)}</ResponsiveGrid> : <View testID="recipe-results-list" accessibilityRole="list">{items.map((item) => <RecipeCard key={item.recipe.id} item={item} onOpen={onOpenDetail} view="list" />)}</View>}
    {nextCursor ? <Button label="Load more recipes" loading={loadingMore} onPress={() => void request(nextCursor)} /> : null}
  </Screen>;
}
