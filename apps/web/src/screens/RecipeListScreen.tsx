import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";
import { Text, TextInput, View } from "react-native";

import { ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, ListRecipesParams, RecipeQueryItem, RecipeSort } from "../api/catalog";
import { RecipeCard } from "../components/RecipeCard";
import { Button, EmptyState, ErrorState, PageHeader, ResponsiveGrid, Screen, Skeleton } from "../components";

type RouteValue = string | string[] | undefined;
type RouteQuery = Record<string, RouteValue>;
const SORTS: RecipeSort[] = ["ingredientCoverage:asc", "ingredientCoverage:desc", "tagCoverage:asc", "tagCoverage:desc", "rating:asc", "rating:desc", "totalMinutes:asc", "totalMinutes:desc", "createdAt:asc", "createdAt:desc", "updatedAt:asc", "updatedAt:desc", "title:asc", "title:desc"];
const DEFAULT_SORT: RecipeSort[] = ["updatedAt:desc"];

function strings(value: RouteValue): string[] { return (Array.isArray(value) ? value : value ? [value] : []).flatMap((entry) => entry.split(",")).map((entry) => entry.trim().replace(/\s+/g, " ")).filter(Boolean); }
function normalizedSet(value: RouteValue): string[] { return [...new Set(strings(value).map((entry) => entry.toLocaleLowerCase()))].sort((a, b) => a.localeCompare(b)); }
function numberValue(value: RouteValue, minimum: number, maximum = Number.MAX_SAFE_INTEGER): number | null { const raw = Array.isArray(value) ? value[0] : value; const parsed = raw === undefined ? Number.NaN : Number(raw); return Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum ? parsed : null; }

export function normalizeRecipeListParams(route: RouteQuery): ListRecipesParams {
  const text = strings(route.text).join(" ").toLocaleLowerCase() || null;
  const availableIngredient = normalizedSet(route.availableIngredient);
  const preferredTag = normalizedSet(route.preferredTag);
  const seenSortFields = new Set<string>();
  const sort = strings(route.sort).filter((entry): entry is RecipeSort => (SORTS as string[]).includes(entry)).filter((entry) => {
    const field = entry.split(":")[0]; if (seenSortFields.has(field)) return false; seenSortFields.add(field); return true;
  }).filter((entry) => entry.split(":")[0] !== "ingredientCoverage" || availableIngredient.length > 0).filter((entry) => entry.split(":")[0] !== "tagCoverage" || preferredTag.length > 0);
  const ratingState = route.ratingState === "rated" || route.ratingState === "unrated" ? route.ratingState : "any";
  const maxTotalMinutes = numberValue(route.maxTotalMinutes, 0);
  const minRating = ratingState === "unrated" ? null : numberValue(route.minRating, 1, 5);
  return {
    ...(text ? { text } : {}), ...(normalizedSet(route.requiredIngredient).length ? { requiredIngredient: normalizedSet(route.requiredIngredient) } : {}),
    ...(availableIngredient.length ? { availableIngredient } : {}), ...(normalizedSet(route.requiredTag).length ? { requiredTag: normalizedSet(route.requiredTag) } : {}),
    ...(preferredTag.length ? { preferredTag } : {}), ...(maxTotalMinutes !== null ? { maxTotalMinutes } : {}), ...(minRating !== null ? { minRating } : {}),
    ...(ratingState !== "any" ? { ratingState } : {}), sort: sort.length ? sort : DEFAULT_SORT, limit: 20,
  };
}

export function serializeRecipeListParams(params: ListRecipesParams): Record<string, string | string[]> {
  const { limit: _limit, cursor: _cursor, ...query } = params;
  const serialized: Record<string, string | string[]> = {};
  for (const key of ["text", "requiredIngredient", "availableIngredient", "requiredTag", "preferredTag", "maxTotalMinutes", "minRating", "ratingState", "sort"] as const) {
    const value = query[key]; if (value === undefined || value === null || value === "" || (Array.isArray(value) && value.length === 0)) continue;
    if (key === "ratingState" && value === "any") continue;
    if (key === "sort" && Array.isArray(value) && value.join("|") === DEFAULT_SORT.join("|")) continue;
    serialized[key] = Array.isArray(value) ? value : String(value);
  }
  return serialized;
}

export type RecipeListScreenProps = { catalog: ReturnType<typeof createCatalogApi>; onOpenDetail(recipeId: string): void; onCreate(): void; onImport(): void; onLogout(): void; onUnauthorized(): void; };

export function RecipeListScreen({ catalog, onOpenDetail, onCreate, onImport, onLogout, onUnauthorized }: RecipeListScreenProps) {
  const router = useRouter(); const route = useLocalSearchParams() as RouteQuery;
  const routeKey = JSON.stringify(route); const params = useMemo(() => normalizeRecipeListParams(route), [routeKey]);
  const queryKey = JSON.stringify(serializeRecipeListParams(params));
  const [items, setItems] = useState<RecipeQueryItem[]>([]); const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true); const [loadingMore, setLoadingMore] = useState(false); const [error, setError] = useState(false); const [view, setView] = useState<"card" | "list">("card");
  const sequence = useRef(0); const mounted = useRef(true); const debounce = useRef<ReturnType<typeof setTimeout> | null>(null); const activeRequest = useRef<AbortController | null>(null);
  void onCreate; void onImport; void onLogout;
  const replaceQuery = useCallback((next: ListRecipesParams) => router.replace({ pathname: "/recipes", params: serializeRecipeListParams(next) }), [router]);
  const request = useCallback(async (cursor: string | null = null) => {
    const current = ++sequence.current; const isMore = cursor !== null;
    if (!isMore) activeRequest.current?.abort();
    const controller = new AbortController();
    if (!isMore) activeRequest.current = controller;
    if (isMore) setLoadingMore(true); else { setLoading(true); setError(false); }
    try { const page = await catalog.listRecipes({ ...params, ...(cursor ? { cursor } : {}) }, { signal: controller.signal }); if (!mounted.current || current !== sequence.current) return;
      setItems((previous) => isMore ? [...new Map([...previous, ...page.items].map((item) => [item.recipe.id, item])).values()] : page.items); setNextCursor(page.nextCursor);
    } catch (err) { if (!mounted.current || current !== sequence.current) return; if (err instanceof ApiUnauthorizedError) onUnauthorized(); else setError(true); }
    finally { if (mounted.current && current === sequence.current) { setLoading(false); setLoadingMore(false); } }
  }, [catalog, onUnauthorized, params]);
  useEffect(() => { mounted.current = true; void request(); return () => { mounted.current = false; activeRequest.current?.abort(); sequence.current += 1; }; }, [request, queryKey]);
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current); }, []);
  const scheduleSearch = (value: string) => { if (debounce.current) clearTimeout(debounce.current); debounce.current = setTimeout(() => replaceQuery(normalizeRecipeListParams({ ...serializeRecipeListParams(params), text: value })), 300); };
  const update = (key: keyof ListRecipesParams, value: string) => replaceQuery(normalizeRecipeListParams({ ...serializeRecipeListParams(params), [key]: value }));
  return <Screen><PageHeader title="Recipes" subtitle={items.length ? `${items.length} recipes loaded` : undefined} />
    <TextInput key={`search-${params.text ?? ""}`} accessibilityLabel="Search recipes" defaultValue={params.text ?? ""} onChangeText={scheduleSearch} placeholder="Search recipes" />
    <TextInput accessibilityLabel="Required ingredients" defaultValue={params.requiredIngredient?.join(", ") ?? ""} onEndEditing={(event) => update("requiredIngredient", event.nativeEvent.text)} placeholder="Required ingredients" />
    <TextInput accessibilityLabel="Available ingredients" defaultValue={params.availableIngredient?.join(", ") ?? ""} onEndEditing={(event) => update("availableIngredient", event.nativeEvent.text)} placeholder="Available ingredients" />
    <TextInput accessibilityLabel="Required tags" defaultValue={params.requiredTag?.join(", ") ?? ""} onEndEditing={(event) => update("requiredTag", event.nativeEvent.text)} placeholder="Required tags" />
    <TextInput accessibilityLabel="Preferred tags" defaultValue={params.preferredTag?.join(", ") ?? ""} onEndEditing={(event) => update("preferredTag", event.nativeEvent.text)} placeholder="Preferred tags" />
    <TextInput accessibilityLabel="Maximum total minutes" defaultValue={params.maxTotalMinutes?.toString() ?? ""} keyboardType="numeric" onEndEditing={(event) => update("maxTotalMinutes", event.nativeEvent.text)} placeholder="Maximum total minutes" />
    <TextInput accessibilityLabel="Minimum rating" defaultValue={params.minRating?.toString() ?? ""} keyboardType="numeric" onEndEditing={(event) => update("minRating", event.nativeEvent.text)} placeholder="Minimum rating" />
    <TextInput accessibilityLabel="Sort order" defaultValue={params.sort?.join(", ") ?? ""} onEndEditing={(event) => update("sort", event.nativeEvent.text)} placeholder="Sort order" />
    <View style={{ flexDirection: "row", gap: 8, marginVertical: 12 }}>{(["any", "rated", "unrated"] as const).map((state) => <Button key={state} label={state === "any" ? "Any rating" : state === "rated" ? "Rated only" : "Unrated only"} variant={(params.ratingState ?? "any") === state ? "primary" : "secondary"} onPress={() => replaceQuery(normalizeRecipeListParams({ ...serializeRecipeListParams(params), ratingState: state }))} />)}</View>
    <View style={{ flexDirection: "row", gap: 8, marginBottom: 16 }}><Button label="Card view" variant={view === "card" ? "primary" : "secondary"} onPress={() => setView("card")} /><Button label="List view" variant={view === "list" ? "primary" : "secondary"} onPress={() => setView("list")} /></View>
    {loading && items.length === 0 ? <ResponsiveGrid>{[0,1,2].map((slot) => <View key={slot} testID="recipe-card-skeleton"><Skeleton height={220} /></View>)}</ResponsiveGrid> : error && items.length === 0 ? <ErrorState title="We couldn't load your recipes. Please try again." action={<Button label="Try again" onPress={() => void request()} />} /> : items.length === 0 ? <EmptyState title="Your recipe library is empty." description="Create or import a recipe to start building your library." /> : view === "card" ? <ResponsiveGrid testID="recipe-results-card">{items.map((item) => <RecipeCard key={item.recipe.id} item={item} onOpen={onOpenDetail} view="card" />)}</ResponsiveGrid> : <View testID="recipe-results-list" accessibilityRole="list">{items.map((item) => <RecipeCard key={item.recipe.id} item={item} onOpen={onOpenDetail} view="list" />)}</View>}
    {nextCursor ? <Button label="Load more recipes" loading={loadingMore} onPress={() => void request(nextCursor)} /> : null}
  </Screen>;
}
