import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocalSearchParams, useRouter } from "expo-router";
import { StyleSheet, TextInput, View } from "react-native";

import { ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import type { createCatalogApi, ListRecipesParams, RecipeQueryItem, RecipeSort } from "../api/catalog";
import { RecipeCard } from "../components/RecipeCard";
import { Button, EmptyState, ErrorState, Field, OfflineBanner, PageHeader, ResponsiveGrid, Screen, Section, Skeleton } from "../components";
import { useTheme } from "../theme/ThemeProvider";
import {
  DEFAULT_SORT,
  normalizeRecipeListParams,
  serializeRecipeListParams,
  type RouteQuery,
} from "./recipeListParams";

export {
  applyCanonicalSelections,
  DEFAULT_SORT,
  dropDependentSorts,
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

function sortChoices(params: ListRecipesParams): { value: RecipeSort; label: string }[] {
  return [
    ...SORT_CHOICES,
    ...(params.availableIngredient?.length ? [{ value: "ingredientCoverage:desc" as const, label: "Best ingredient match" }] : []),
    ...(params.preferredTag?.length ? [{ value: "tagCoverage:desc" as const, label: "Best tag match" }] : []),
  ];
}

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
  const routeSearchText = params.text ?? ""; const [items, setItems] = useState<RecipeQueryItem[]>([]); const [nextCursor, setNextCursor] = useState<string | null>(null); const [loading, setLoading] = useState(true); const [loadingMore, setLoadingMore] = useState(false); const [error, setError] = useState<LibraryError>("none"); const [view, setView] = useState<"card" | "list">("card"); const [searchDraft, setSearchDraft] = useState(routeSearchText);
  const mounted = useRef(true); const requestId = useRef(0); const debounce = useRef<ReturnType<typeof setTimeout> | null>(null); const draftGeneration = useRef(0); const controller = useRef<AbortController | null>(null); const paginationGuard = useRef(createPaginationRequestGuard());
  const previousRouteSearchText = useRef(routeSearchText);
  const { theme } = useTheme();
  const currentSort = params.sort?.[0] ?? DEFAULT_SORT[0];
  const choices = sortChoices(params);
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
        <ResponsiveGrid minItemWidth={240}>
          <Field label="Required ingredients" hint="Must include every item, comma-separated" control={<TextInput value={params.requiredIngredient?.join(", ") ?? ""} onEndEditing={(event) => update("requiredIngredient", event.nativeEvent.text)} placeholder="tomato, basil" placeholderTextColor={placeholder} />} />
          <Field label="Available ingredients" hint="What you have on hand, comma-separated" control={<TextInput value={params.availableIngredient?.join(", ") ?? ""} onEndEditing={(event) => update("availableIngredient", event.nativeEvent.text)} placeholder="onion, garlic" placeholderTextColor={placeholder} />} />
          <Field label="Required tags" hint="Must include every tag, comma-separated" control={<TextInput value={params.requiredTag?.join(", ") ?? ""} onEndEditing={(event) => update("requiredTag", event.nativeEvent.text)} placeholder="vegan, weeknight" placeholderTextColor={placeholder} />} />
          <Field label="Preferred tags" hint="Nice to have, comma-separated" control={<TextInput value={params.preferredTag?.join(", ") ?? ""} onEndEditing={(event) => update("preferredTag", event.nativeEvent.text)} placeholder="family" placeholderTextColor={placeholder} />} />
          <Field label="Maximum total minutes" hint="Whole minutes only" control={<TextInput value={params.maxTotalMinutes?.toString() ?? ""} keyboardType="numeric" onEndEditing={(event) => update("maxTotalMinutes", event.nativeEvent.text)} placeholder="30" placeholderTextColor={placeholder} />} />
          <Field label="Minimum rating" hint="1 to 5" control={<TextInput value={params.minRating?.toString() ?? ""} keyboardType="numeric" onEndEditing={(event) => update("minRating", event.nativeEvent.text)} placeholder="4" placeholderTextColor={placeholder} />} />
        </ResponsiveGrid>
      </Section>
      <Section title="Sort">
        <View style={styles.chipRow}>
          {choices.map((choice) => (
            <Button
              key={choice.value}
              label={choice.label}
              variant={currentSort === choice.value ? "primary" : "secondary"}
              accessibilityState={{ selected: currentSort === choice.value }}
              onPress={() => navigate(normalizeRecipeListParams({ ...serializeRecipeListParams(params), sort: choice.value }))}
            />
          ))}
        </View>
      </Section>
      <View style={styles.chipRow}>
        {(["any", "rated", "unrated"] as const).map((state) => (
          <Button
            key={state}
            label={state === "any" ? "Any rating" : state === "rated" ? "Rated only" : "Unrated only"}
            variant={(params.ratingState ?? "any") === state ? "primary" : "secondary"}
            accessibilityState={{ selected: (params.ratingState ?? "any") === state }}
            onPress={() => navigate(normalizeRecipeListParams({ ...serializeRecipeListParams(params), ratingState: state }))}
          />
        ))}
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
              ? <ResponsiveGrid testID="recipe-results-card">{items.map((item) => <RecipeCard key={item.recipe.id} item={item} onOpen={onOpenDetail} view="card" />)}</ResponsiveGrid>
              : <View testID="recipe-results-list" accessibilityRole="list">{items.map((item) => <RecipeCard key={item.recipe.id} item={item} onOpen={onOpenDetail} view="list" />)}</View>}
      {nextCursor ? <Button label="Load more recipes" loading={loadingMore} onPress={() => void request(nextCursor)} /> : null}
    </Screen>
  );
}

const styles = StyleSheet.create({
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 12 },
  viewToggle: { marginBottom: 16 },
});
