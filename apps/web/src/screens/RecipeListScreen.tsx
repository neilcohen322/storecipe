import { useCallback, useEffect, useMemo, useRef, useState, type ComponentRef, type RefObject } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";
import { StyleSheet, TextInput, View } from "react-native";

import { ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, ListRecipesParams, Recipe, RecipeSort } from "../api/catalog";
import type { FacetPickerSelection } from "../components/FacetPicker";
import { DurationFilter } from "../components/DurationFilter";
import { FacetPicker } from "../components/FacetPicker";
import { FilterDialog, type FilterDraft } from "../components/FilterDialog";
import { RatingFilter } from "../components/RatingFilter";
import { RecipeCard } from "../components/RecipeCard";
import { SortMenu } from "../components/SortMenu";
import { Button, EmptyState, ErrorState, Field, InlineNotice, OfflineBanner, PageHeader, ResponsiveGrid, Screen, Skeleton } from "../components";
import type { LayoutMode } from "../navigation/types";
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
type TriggerRef = ComponentRef<typeof Button>;
type FocusableRef = RefObject<{ focus: () => void } | null>;

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

function emptyDraft(): FilterDraft {
  return {};
}

function draftFromParams(params: ListRecipesParams): FilterDraft {
  return {
    ingredient: params.ingredient,
    tag: params.tag,
    maxTotalMinutes: params.maxTotalMinutes,
    minRating: params.minRating,
    ratingState: params.ratingState ?? "any",
  };
}

export function activeFilterCount(source: FilterDraft): number {
  return (source.ingredient?.length ?? 0)
    + (source.tag?.length ?? 0)
    + (source.maxTotalMinutes != null ? 1 : 0)
    + (source.minRating != null ? 1 : 0)
    + (source.ratingState && source.ratingState !== "any" ? 1 : 0);
}

function decorateDraftChips(values: string[] | undefined, committed: FacetPickerSelection[]): FacetPickerSelection[] {
  if (!values?.length) return [];
  const byName = new Map(committed.map((item) => [item.name, item]));
  return values.map((name) => byName.get(name) ?? { name });
}

function sameSerialized(left: ListRecipesParams, right: ListRecipesParams): boolean {
  return JSON.stringify(serializeRecipeListParams(left)) === JSON.stringify(serializeRecipeListParams(right));
}

export type RecipeListScreenProps = {
  catalog: ReturnType<typeof createCatalogApi>;
  onOpenDetail(recipeId: string): void;
  onCreate(): void;
  onImport(): void;
  onLogout(): void;
  onUnauthorized(): void;
  layoutMode?: LayoutMode;
};

export function RecipeListScreen({
  catalog,
  onOpenDetail,
  onCreate,
  onImport,
  onLogout,
  onUnauthorized,
  layoutMode = "medium",
}: RecipeListScreenProps) {
  const router = useRouter();
  const route = useLocalSearchParams() as RouteQuery;
  const routeKey = JSON.stringify(route);
  const params = useMemo(() => normalizeRecipeListParams(route), [routeKey]);
  const queryKey = JSON.stringify(serializeRecipeListParams(params));
  const routeSearchText = params.text ?? "";
  const [items, setItems] = useState<Recipe[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<LibraryError>("none");
  const [view, setView] = useState<"card" | "list">("card");
  const [searchDraft, setSearchDraft] = useState(routeSearchText);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const [draft, setDraft] = useState<FilterDraft>(emptyDraft);
  const mounted = useRef(true);
  const requestId = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const paginationGuard = useRef(createPaginationRequestGuard());
  const previousRouteSearchText = useRef(routeSearchText);
  const filtersTriggerRef = useRef<TriggerRef | null>(null);
  const sortTriggerRef = useRef<TriggerRef | null>(null);
  const { theme } = useTheme();
  const currentSort = params.sort?.[0] ?? DEFAULT_SORT[0];
  if (previousRouteSearchText.current !== routeSearchText) {
    previousRouteSearchText.current = routeSearchText;
    setSearchDraft(routeSearchText);
  }
  void onCreate; void onImport; void onLogout;
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;
  const pushFilters = useCallback((next: ListRecipesParams) => router.push({ pathname: "/recipes", params: serializeRecipeListParams(next) }), [router]);
  const replaceFilters = useCallback((next: ListRecipesParams) => router.replace({ pathname: "/recipes", params: serializeRecipeListParams(next) }), [router]);
  const options = useRecipeFacetOptions({ catalog, onUnauthorized });
  const selections = useResolvedRecipeSelections({ catalog, params, replaceFilters, onUnauthorized });
  const facetError = options.facetError !== "none" || selections.facetError !== "none";
  const committedCount = activeFilterCount(draftFromParams(params));
  const previousFiltersOpen = useRef(false);

  useEffect(() => {
    if (filtersOpen && !previousFiltersOpen.current) options.browse();
    previousFiltersOpen.current = filtersOpen;
  }, [filtersOpen, options.browse]);

  const retryFacets = () => {
    options.retryFacets();
    selections.retrySelections();
  };

  const addDraftValue = (key: LaneId, name: string) => {
    const current = draft[key] ?? [];
    const limit = key === "ingredient" ? MAX_INGREDIENT_FILTERS : MAX_TAG_FILTERS;
    if (current.length >= limit || current.includes(name)) return;
    setDraft({ ...draft, [key]: [...current, name] });
  };
  const removeDraftValue = (key: LaneId, name: string) => {
    setDraft({ ...draft, [key]: (draft[key] ?? []).filter((entry) => entry !== name) });
  };

  const submitSearch = () => {
    const next = normalizeRecipeListParams({
      ...serializeRecipeListParams(params),
      text: searchDraft,
    });
    if (sameSerialized(next, params)) return;
    pushFilters(next);
  };

  const applyFilters = () => {
    const next = normalizeRecipeListParams({
      ...serializeRecipeListParams(params),
      ingredient: draft.ingredient,
      tag: draft.tag,
      maxTotalMinutes: draft.maxTotalMinutes == null ? undefined : String(draft.maxTotalMinutes),
      minRating: draft.minRating == null ? undefined : String(draft.minRating),
      ratingState: draft.ratingState,
    });
    pushFilters(next);
    setFiltersOpen(false);
  };

  const openFilters = () => {
    setDraft(draftFromParams(params));
    setFiltersOpen(true);
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

  const errorContent = error === "offline"
    ? <><OfflineBanner message="You’re offline. Check your connection and try again." /><Button label="Try again" onPress={() => void request()} /></>
    : <ErrorState title="We couldn't load your recipes. Please try again." description="Your library is still here. Retry when you're ready." action={<Button label="Try again" onPress={() => void request()} />} />;
  const placeholder = theme.colors.mutedText;
  const filtersLabel = committedCount ? `Filters (${committedCount})` : "Filters";

  return (
    <>
    <Screen accessibilityElementsHidden={filtersOpen || sortOpen} importantForAccessibility={filtersOpen || sortOpen ? "no-hide-descendants" : "auto"}>
      <PageHeader title="Recipes" subtitle={items.length ? `${items.length} recipes loaded` : undefined} />
      <View style={styles.toolbar}>
        <View style={styles.searchField}>
          <Field
            label="Search recipes"
            hint="Search titles and recipe text"
            control={
              <TextInput
                value={searchDraft}
                onChangeText={setSearchDraft}
                onSubmitEditing={submitSearch}
                returnKeyType="search"
                placeholder="Tomato soup"
                placeholderTextColor={placeholder}
              />
            }
          />
        </View>
        <Button label="Search" onPress={submitSearch} />
        <Button ref={filtersTriggerRef} label={filtersLabel} onPress={openFilters} />
        <Button ref={sortTriggerRef} label="Sort" onPress={() => setSortOpen(true)} />
      </View>
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
    <FilterDialog
        visible={filtersOpen}
        layoutMode={layoutMode}
        draft={draft}
        onChange={setDraft}
        onApply={applyFilters}
        onClear={() => setDraft(emptyDraft())}
        onDismiss={() => setFiltersOpen(false)}
        returnFocusRef={filtersTriggerRef as FocusableRef}
      >
        {facetError ? (
          <>
            <InlineNotice tone="error" message="We couldn't load filter options. Please try again." />
            <Button label="Try filters again" onPress={retryFacets} />
          </>
        ) : null}
        <FacetPicker
          label="Ingredients"
          hint="Must include every selected ingredient"
          selected={decorateDraftChips(draft.ingredient, selections.selected.ingredient)}
          options={options.lanes.ingredient.options}
          search={options.lanes.ingredient.search}
          onSearch={(value) => options.searchLane("ingredient", value)}
          hasMore={Boolean(options.lanes.ingredient.nextCursor)}
          loadingMore={options.lanes.ingredient.loadingMore}
          onLoadMore={() => options.loadMore("ingredient")}
          loading={options.lanes.ingredient.loading}
          addDisabled={(draft.ingredient?.length ?? 0) >= MAX_INGREDIENT_FILTERS}
          onAdd={(name) => addDraftValue("ingredient", name)}
          onRemove={(name) => removeDraftValue("ingredient", name)}
        />
        <FacetPicker
          label="Tags"
          hint="Must include every selected tag"
          selected={decorateDraftChips(draft.tag, selections.selected.tag)}
          options={options.lanes.tag.options}
          search={options.lanes.tag.search}
          onSearch={(value) => options.searchLane("tag", value)}
          hasMore={Boolean(options.lanes.tag.nextCursor)}
          loadingMore={options.lanes.tag.loadingMore}
          onLoadMore={() => options.loadMore("tag")}
          loading={options.lanes.tag.loading}
          addDisabled={(draft.tag?.length ?? 0) >= MAX_TAG_FILTERS}
          onAdd={(name) => addDraftValue("tag", name)}
          onRemove={(name) => removeDraftValue("tag", name)}
        />
        <DurationFilter
          observed={options.observedMinutes}
          value={draft.maxTotalMinutes ?? null}
          onChange={(value) => setDraft({ ...draft, maxTotalMinutes: value })}
        />
        <RatingFilter
          minRating={draft.minRating ?? null}
          ratingState={draft.ratingState ?? "any"}
          onMinRating={(value) => setDraft({ ...draft, minRating: value })}
          onRatingState={(value) => setDraft({ ...draft, ratingState: value, ...(value === "unrated" ? { minRating: null } : {}) })}
        />
      </FilterDialog>
      <SortMenu
        visible={sortOpen}
        value={currentSort}
        options={SORT_CHOICES}
        onSelect={(value) => {
          const next = normalizeRecipeListParams({ ...serializeRecipeListParams(params), sort: value });
          if (!sameSerialized(next, params)) pushFilters(next);
        }}
        onDismiss={() => setSortOpen(false)}
        returnFocusRef={sortTriggerRef as FocusableRef}
      />
    </>
  );
}

const styles = StyleSheet.create({
  toolbar: { flexDirection: "row", flexWrap: "wrap", alignItems: "flex-end", gap: 8, marginBottom: 12 },
  searchField: { flexGrow: 1, flexBasis: 220, minWidth: 0 },
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 },
  viewToggle: { marginBottom: 16 },
});
