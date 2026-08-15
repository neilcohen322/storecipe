import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";
import { StyleSheet, TextInput, View } from "react-native";

import { ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, ListRecipesParams, Recipe, RecipeSort } from "../api/catalog";
import { DurationFilter } from "../components/DurationFilter";
import { FacetPicker } from "../components/FacetPicker";
import { RatingFilter } from "../components/RatingFilter";
import { RecipeCard } from "../components/RecipeCard";
import { Button, EmptyState, ErrorState, Field, InlineNotice, OfflineBanner, PageHeader, ResponsiveGrid, Screen, Section, Skeleton } from "../components";
import { useTheme } from "../theme/ThemeProvider";
import {
  DEFAULT_SORT,
  MAX_INGREDIENT_FILTERS,
  MAX_TAG_FILTERS,
  normalizeRecipeListParams,
  serializeRecipeListParams,
  type RouteQuery,
} from "./recipeListParams";
import { useRecipeFacetOptions, type LaneId } from "./useRecipeFacetOptions";
import { useResolvedRecipeSelections } from "./useResolvedRecipeSelections";

export {
  applyCanonicalSelections,
  DEFAULT_SORT,
  normalizeRecipeListParams,
  routeStrings,
  sameStringList,
  serializeRecipeListParams,
} from "./recipeListParams";
type LibraryError = "none" | "offline" | "generic";
const SORT_CHOICES: { value: RecipeSort; label: string }[] = [
  { value: "updatedAt:desc", label: "Recently updated" },
  { value: "createdAt:desc", label: "Newest" },
  { value: "title:asc", label: "Title A to Z" },
  { value: "title:desc", label: "Title Z to A" },
  { value: "rating:desc", label: "Highest rated" },
  { value: "rating:asc", label: "Lowest rated" },
  { value: "totalMinutes:asc", label: "Shortest time" },
  { value: "totalMinutes:desc", label: "Longest time" },
];

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

export type RecipeListScreenProps = { catalog: ReturnType<typeof createCatalogApi>; onOpenDetail(recipeId: string): void; onCreate(): void; onImport(): void; onLogout(): void; onUnauthorized(): void; };

export function RecipeListScreen({ catalog, onOpenDetail, onCreate, onImport, onLogout, onUnauthorized }: RecipeListScreenProps) {
  const router = useRouter(); const route = useLocalSearchParams() as RouteQuery; const routeKey = JSON.stringify(route);
  const params = useMemo(() => normalizeRecipeListParams(route), [routeKey]); const queryKey = JSON.stringify(serializeRecipeListParams(params));
  const routeSearchText = params.text ?? ""; const [items, setItems] = useState<Recipe[]>([]); const [nextCursor, setNextCursor] = useState<string | null>(null); const [loading, setLoading] = useState(true); const [loadingMore, setLoadingMore] = useState(false); const [error, setError] = useState<LibraryError>("none"); const [view, setView] = useState<"card" | "list">("card"); const [searchDraft, setSearchDraft] = useState(routeSearchText);
  const mounted = useRef(true); const requestId = useRef(0); const debounce = useRef<ReturnType<typeof setTimeout> | null>(null); const draftGeneration = useRef(0); const controller = useRef<AbortController | null>(null); const paginationGuard = useRef(createPaginationRequestGuard());
  const previousRouteSearchText = useRef(routeSearchText);
  const { theme } = useTheme();
  const currentSort = params.sort?.[0] ?? DEFAULT_SORT[0];
  if (previousRouteSearchText.current !== routeSearchText) { previousRouteSearchText.current = routeSearchText; draftGeneration.current += 1; setSearchDraft(routeSearchText); }
  void onCreate; void onImport; void onLogout;
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;
  const pushFilters = useCallback((next: ListRecipesParams) => router.push({ pathname: "/recipes", params: serializeRecipeListParams(next) }), [router]);
  const replaceFilters = useCallback((next: ListRecipesParams) => router.replace({ pathname: "/recipes", params: serializeRecipeListParams(next) }), [router]);
  const options = useRecipeFacetOptions({ catalog, onUnauthorized });
  const selections = useResolvedRecipeSelections({ catalog, params, replaceFilters, onUnauthorized });
  const facetError = options.facetError !== "none" || selections.facetError !== "none";
  const retryFacets = () => {
    options.retryFacets();
    selections.retrySelections();
  };
  const commitFilters = (patch: Record<string, string | string[] | undefined>) => {
    pushFilters(normalizeRecipeListParams({ ...serializeRecipeListParams(params), ...patch }));
  };
  const addFilterValue = (key: LaneId, name: string) => {
    const current = params[key] ?? [];
    const limit = key === "ingredient" ? MAX_INGREDIENT_FILTERS : MAX_TAG_FILTERS;
    if (current.length >= limit) return;
    commitFilters({ [key]: [...current, name] });
  };
  const removeFilterValue = (key: LaneId, name: string) => {
    const bucket = params[key] ?? [];
    const chips = selections.selected[key];
    commitFilters({
      [key]: bucket.filter((entry, index) => entry !== name && chips[index]?.name !== name),
    });
  };
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
      setItems((current) => pagination ? [...new Map([...current, ...page.items].map((item) => [item.id, item])).values()] : page.items);
      setNextCursor(page.nextCursor);
    } catch (caught) {
      if (!mounted.current || id !== requestId.current || isAbortError(caught)) return;
      if (caught instanceof ApiUnauthorizedError) onUnauthorizedRef.current(); else setError(isOfflineError(caught) ? "offline" : "generic");
    } finally {
      if (pagination) paginationGuard.current.finish(id);
      if (mounted.current && id === requestId.current) { setLoading(false); setLoadingMore(false); }
    }
  }, [catalog, params]);
  useEffect(() => { mounted.current = true; void request(); return () => { mounted.current = false; controller.current?.abort(); requestId.current += 1; paginationGuard.current.reset(); }; }, [queryKey, request]);
  useEffect(() => () => { if (debounce.current) clearTimeout(debounce.current); }, []);
  const scheduleSearch = (text: string) => { setSearchDraft(text); if (debounce.current) clearTimeout(debounce.current); const generation = ++draftGeneration.current; debounce.current = setTimeout(() => { if (draftGeneration.current === generation) pushFilters(normalizeRecipeListParams({ ...serializeRecipeListParams(params), text })); }, 300); };
  const errorContent = error === "offline"
    ? <><OfflineBanner message="You’re offline. Check your connection and try again." /><Button label="Try again" onPress={() => void request()} /></>
    : <ErrorState title="We couldn't load your recipes. Please try again." description="Your library is still here. Retry when you're ready." action={<Button label="Try again" onPress={() => void request()} />} />;
  const placeholder = theme.colors.mutedText;
  return (
    <Screen>
      <PageHeader title="Recipes" subtitle={items.length ? `${items.length} recipes loaded` : undefined} />
      <Field
        label="Search recipes"
        hint="Search titles and recipe text"
        control={<TextInput value={searchDraft} onChangeText={scheduleSearch} placeholder="Tomato soup" placeholderTextColor={placeholder} />}
      />
      <Section title="Filters">
        {facetError ? (
          <>
            <InlineNotice tone="error" message="We couldn't load filter options. Please try again." />
            <Button label="Try filters again" onPress={retryFacets} />
          </>
        ) : null}
        <ResponsiveGrid minItemWidth={240}>
          <FacetPicker
            label="Ingredients"
            hint="Must include every selected ingredient"
            selected={selections.selected.ingredient}
            options={options.lanes.ingredient.options}
            search={options.lanes.ingredient.search}
            onSearch={(value) => options.searchLane("ingredient", value)}
            hasMore={Boolean(options.lanes.ingredient.nextCursor)}
            loadingMore={options.lanes.ingredient.loadingMore}
            onLoadMore={() => options.loadMore("ingredient")}
            loading={options.lanes.ingredient.loading}
            onAdd={(name) => addFilterValue("ingredient", name)}
            onRemove={(name) => removeFilterValue("ingredient", name)}
          />
          <FacetPicker
            label="Tags"
            hint="Must include every selected tag"
            selected={selections.selected.tag}
            options={options.lanes.tag.options}
            search={options.lanes.tag.search}
            onSearch={(value) => options.searchLane("tag", value)}
            hasMore={Boolean(options.lanes.tag.nextCursor)}
            loadingMore={options.lanes.tag.loadingMore}
            onLoadMore={() => options.loadMore("tag")}
            loading={options.lanes.tag.loading}
            onAdd={(name) => addFilterValue("tag", name)}
            onRemove={(name) => removeFilterValue("tag", name)}
          />
          <DurationFilter
            observed={options.observedMinutes}
            value={params.maxTotalMinutes ?? null}
            onChange={(value) => commitFilters({ maxTotalMinutes: value === null ? undefined : String(value) })}
          />
          <RatingFilter
            minRating={params.minRating ?? null}
            ratingState={params.ratingState ?? "any"}
            onMinRating={(value) => commitFilters({ minRating: value === null ? undefined : String(value) })}
            onRatingState={(value) => commitFilters({ ratingState: value })}
          />
        </ResponsiveGrid>
      </Section>
      <Section title="Sort">
        <View style={styles.chipRow}>
          {SORT_CHOICES.map((choice) => (
            <Button
              key={choice.value}
              label={choice.label}
              variant={currentSort === choice.value ? "primary" : "secondary"}
              accessibilityState={{ selected: currentSort === choice.value }}
              onPress={() => commitFilters({ sort: choice.value })}
            />
          ))}
        </View>
      </Section>
      <View style={[styles.chipRow, styles.viewToggle]}>
        <Button label="Card view" variant={view === "card" ? "primary" : "secondary"} onPress={() => setView("card")} />
        <Button label="List view" variant={view === "list" ? "primary" : "secondary"} onPress={() => setView("list")} />
      </View>
      {loading && items.length === 0
        ? <ResponsiveGrid>{[0, 1, 2].map((slot) => <View key={slot} testID="recipe-card-skeleton"><Skeleton height={220} /></View>)}</ResponsiveGrid>
        : error !== "none" && items.length === 0
          ? errorContent
          : items.length === 0
            ? <EmptyState title="Your recipe library is empty." description="Create or import a recipe to start building your library." />
            : view === "card"
              ? <ResponsiveGrid testID="recipe-results-card">{items.map((item) => <RecipeCard key={item.id} item={item} onOpen={onOpenDetail} view="card" />)}</ResponsiveGrid>
              : <View testID="recipe-results-list" accessibilityRole="list">{items.map((item) => <RecipeCard key={item.id} item={item} onOpen={onOpenDetail} view="list" />)}</View>}
      {nextCursor ? <Button label="Load more recipes" loading={loadingMore} onPress={() => void request(nextCursor)} /> : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 },
  viewToggle: { marginBottom: 16 },
});
