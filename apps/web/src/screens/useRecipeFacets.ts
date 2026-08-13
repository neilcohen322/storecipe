import { useCallback, useEffect, useRef, useState } from "react";
import { useFocusEffect } from "expo-router";

import { ApiError, ApiUnauthorizedError } from "../api/client";
import type {
  createCatalogApi,
  ListRecipesParams,
  RecipeFacetBrowseParams,
  RecipeFacetPage,
  RecipeFacetSelectionsResponse,
} from "../api/catalog";
import type { FacetPickerSelection } from "../components/FacetPicker";
import { applyCanonicalSelections, sameStringList } from "./recipeListParams";

export type LaneId = "requiredIngredient" | "availableIngredient" | "requiredTag" | "preferredTag";

export type LaneState = {
  search: string;
  options: string[];
  nextCursor: string | null;
  loading: boolean;
  loadingMore: boolean;
};

type LaneRuntime = {
  generation: number;
  controller: AbortController | null;
  debounce: ReturnType<typeof setTimeout> | null;
};

const INGREDIENT_LANES: LaneId[] = ["requiredIngredient", "availableIngredient"];
const TAG_LANES: LaneId[] = ["requiredTag", "preferredTag"];
const ALL_LANES: LaneId[] = [...INGREDIENT_LANES, ...TAG_LANES];

function emptyLane(): LaneState {
  return { search: "", options: [], nextCursor: null, loading: false, loadingMore: false };
}

function emptyLanes(): Record<LaneId, LaneState> {
  return {
    requiredIngredient: emptyLane(),
    availableIngredient: emptyLane(),
    requiredTag: emptyLane(),
    preferredTag: emptyLane(),
  };
}

function isIngredientLane(id: LaneId): boolean {
  return id === "requiredIngredient" || id === "availableIngredient";
}

function uniqueFirstSeen(existing: string[], incoming: string[]): string[] {
  const seen = new Set(existing);
  const next = [...existing];
  for (const name of incoming) {
    if (!seen.has(name)) {
      seen.add(name);
      next.push(name);
    }
  }
  return next;
}

function uniqueNames(values: string[]): string[] {
  return [...new Set(values)];
}

export function ingredientTagKey(params: ListRecipesParams): string {
  return JSON.stringify({
    requiredIngredient: params.requiredIngredient ?? [],
    availableIngredient: params.availableIngredient ?? [],
    requiredTag: params.requiredTag ?? [],
    preferredTag: params.preferredTag ?? [],
  });
}

function bucketsDiffer(current: ListRecipesParams, next: ListRecipesParams): boolean {
  return (
    !sameStringList(current.requiredIngredient ?? [], next.requiredIngredient ?? [])
    || !sameStringList(current.availableIngredient ?? [], next.availableIngredient ?? [])
    || !sameStringList(current.requiredTag ?? [], next.requiredTag ?? [])
    || !sameStringList(current.preferredTag ?? [], next.preferredTag ?? [])
  );
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function browseParamsForLane(id: LaneId, search: string, cursor: string | null): RecipeFacetBrowseParams {
  if (isIngredientLane(id)) {
    return {
      ingredientLimit: 200,
      ...(search ? { ingredientQ: search } : {}),
      ...(cursor ? { ingredientCursor: cursor } : {}),
    };
  }
  return {
    tagLimit: 200,
    ...(search ? { tagQ: search } : {}),
    ...(cursor ? { tagCursor: cursor } : {}),
  };
}

function pageNames(id: LaneId, page: RecipeFacetPage): { names: string[]; nextCursor: string | null } {
  if (isIngredientLane(id)) {
    return { names: page.ingredients, nextCursor: page.ingredientNextCursor };
  }
  return { names: page.tags, nextCursor: page.tagNextCursor };
}

function selectedFor(
  values: string[] | undefined,
  items: RecipeFacetSelectionsResponse["ingredients"] | null,
): FacetPickerSelection[] {
  if (!values?.length) return [];
  if (!items) {
    return values.map((name) => ({ name }));
  }
  const byRequested = new Map(items.map((item) => [item.requestedName, item]));
  return values.map((name) => {
    const resolved = byRequested.get(name);
    if (!resolved) return { name };
    if (resolved.observed) return { name: resolved.normalizedName };
    return { name: resolved.requestedName, unavailable: true };
  });
}

export type UseRecipeFacetsArgs = {
  catalog: ReturnType<typeof createCatalogApi>;
  params: ListRecipesParams;
  replaceFilters: (next: ListRecipesParams) => void;
  onUnauthorized: () => void;
};

export function useRecipeFacets({ catalog, params, replaceFilters, onUnauthorized }: UseRecipeFacetsArgs) {
  const [lanes, setLanes] = useState<Record<LaneId, LaneState>>(emptyLanes);
  const [observedMinutes, setObservedMinutes] = useState<{ min: number; max: number } | null>(null);
  const [facetError, setFacetError] = useState<"none" | "generic">("none");
  const [resolution, setResolution] = useState<RecipeFacetSelectionsResponse | null>(null);

  const paramsRef = useRef(params);
  paramsRef.current = params;
  const catalogRef = useRef(catalog);
  catalogRef.current = catalog;
  const replaceFiltersRef = useRef(replaceFilters);
  replaceFiltersRef.current = replaceFilters;
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  const runtimes = useRef<Record<LaneId, LaneRuntime>>({
    requiredIngredient: { generation: 0, controller: null, debounce: null },
    availableIngredient: { generation: 0, controller: null, debounce: null },
    requiredTag: { generation: 0, controller: null, debounce: null },
    preferredTag: { generation: 0, controller: null, debounce: null },
  });
  const resolveRuntime = useRef({ generation: 0, controller: null as AbortController | null });
  const focusRuntime = useRef({ generation: 0, controller: null as AbortController | null });
  const previousKey = useRef(ingredientTagKey(params));
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      for (const id of ALL_LANES) {
        const runtime = runtimes.current[id];
        if (runtime.debounce) clearTimeout(runtime.debounce);
        runtime.controller?.abort();
      }
      focusRuntime.current.controller?.abort();
      resolveRuntime.current.controller?.abort();
    };
  }, []);

  const patchLane = (id: LaneId, patch: Partial<LaneState> | ((current: LaneState) => LaneState)) => {
    setLanes((current) => ({
      ...current,
      [id]: typeof patch === "function" ? patch(current[id]) : { ...current[id], ...patch },
    }));
  };

  const handleUnauthorized = (error: unknown): boolean => {
    if (error instanceof ApiUnauthorizedError) {
      onUnauthorizedRef.current();
      return true;
    }
    return false;
  };

  const fetchLane = useCallback(async (id: LaneId, search: string, cursor: string | null, mode: "replace" | "append" | "restart") => {
    const runtime = runtimes.current[id];
    runtime.controller?.abort();
    const generation = ++runtime.generation;
    const controller = new AbortController();
    runtime.controller = controller;
    if (mode === "append") {
      patchLane(id, { loadingMore: true });
    } else if (mode === "restart") {
      patchLane(id, { options: [], nextCursor: null, loading: true, loadingMore: false });
    } else {
      patchLane(id, { loading: true, loadingMore: false });
    }
    try {
      const page = await catalogRef.current.listRecipeFacets(browseParamsForLane(id, search, cursor), { signal: controller.signal });
      if (!mounted.current || generation !== runtime.generation) return;
      const { names, nextCursor } = pageNames(id, page);
      patchLane(id, (current) => ({
        ...current,
        options: mode === "append" ? uniqueFirstSeen(current.options, names) : names,
        nextCursor,
        loading: false,
        loadingMore: false,
      }));
    } catch (error) {
      if (!mounted.current || generation !== runtime.generation || isAbortError(error)) return;
      if (handleUnauthorized(error)) {
        patchLane(id, { loading: false, loadingMore: false });
        return;
      }
      if (error instanceof ApiError && error.status === 409 && cursor) {
        void fetchLane(id, search, null, "restart");
        return;
      }
      setFacetError("generic");
      patchLane(id, { loading: false, loadingMore: false });
    }
  }, []);

  const loadFocusFacets = useCallback(async () => {
    const generation = ++focusRuntime.current.generation;
    focusRuntime.current.controller?.abort();
    const controller = new AbortController();
    focusRuntime.current.controller = controller;
    for (const id of ALL_LANES) {
      const runtime = runtimes.current[id];
      runtime.controller?.abort();
      runtime.generation += 1;
      if (runtime.debounce) {
        clearTimeout(runtime.debounce);
        runtime.debounce = null;
      }
    }
    setLanes({
      requiredIngredient: { ...emptyLane(), loading: true },
      availableIngredient: { ...emptyLane(), loading: true },
      requiredTag: { ...emptyLane(), loading: true },
      preferredTag: { ...emptyLane(), loading: true },
    });
    setFacetError("none");
    try {
      const page = await catalogRef.current.listRecipeFacets({}, { signal: controller.signal });
      if (!mounted.current || generation !== focusRuntime.current.generation) return;
      setObservedMinutes(page.totalMinutes);
      setLanes({
        requiredIngredient: { search: "", options: page.ingredients, nextCursor: page.ingredientNextCursor, loading: false, loadingMore: false },
        availableIngredient: { search: "", options: page.ingredients, nextCursor: page.ingredientNextCursor, loading: false, loadingMore: false },
        requiredTag: { search: "", options: page.tags, nextCursor: page.tagNextCursor, loading: false, loadingMore: false },
        preferredTag: { search: "", options: page.tags, nextCursor: page.tagNextCursor, loading: false, loadingMore: false },
      });
    } catch (error) {
      if (!mounted.current || generation !== focusRuntime.current.generation || isAbortError(error)) return;
      if (handleUnauthorized(error)) {
        setLanes(emptyLanes());
        return;
      }
      setFacetError("generic");
      setLanes(emptyLanes());
    }
  }, []);

  const resolveSelections = useCallback(async () => {
    const requestKey = ingredientTagKey(paramsRef.current);
    const runtime = resolveRuntime.current;
    runtime.controller?.abort();
    const generation = ++runtime.generation;
    const controller = new AbortController();
    runtime.controller = controller;
    const current = paramsRef.current;
    try {
      const result = await catalogRef.current.resolveRecipeFacetSelections({
        ingredients: uniqueNames([...(current.requiredIngredient ?? []), ...(current.availableIngredient ?? [])]),
        tags: uniqueNames([...(current.requiredTag ?? []), ...(current.preferredTag ?? [])]),
      }, { signal: controller.signal });
      if (!mounted.current || generation !== runtime.generation) return;
      if (ingredientTagKey(paramsRef.current) !== requestKey) return;
      setResolution(result);
      const next = applyCanonicalSelections(paramsRef.current, result);
      if (bucketsDiffer(paramsRef.current, next)) {
        replaceFiltersRef.current(next);
      }
    } catch (error) {
      if (!mounted.current || generation !== runtime.generation || isAbortError(error)) return;
      if (handleUnauthorized(error)) return;
      setFacetError("generic");
    }
  }, []);

  useFocusEffect(useCallback(() => {
    void loadFocusFacets();
    void resolveSelections();
  }, [loadFocusFacets, resolveSelections]));

  const currentKey = ingredientTagKey(params);
  useEffect(() => {
    if (previousKey.current === currentKey) return;
    previousKey.current = currentKey;
    setResolution(null);
    void resolveSelections();
  }, [currentKey, resolveSelections]);

  const searchLane = (id: LaneId, value: string) => {
    const runtime = runtimes.current[id];
    patchLane(id, { search: value });
    if (runtime.debounce) clearTimeout(runtime.debounce);
    runtime.debounce = setTimeout(() => {
      void fetchLane(id, value, null, "replace");
    }, 300);
  };

  const loadMore = (id: LaneId) => {
    const lane = lanes[id];
    if (!lane.nextCursor || lane.loadingMore) return;
    void fetchLane(id, lane.search, lane.nextCursor, "append");
  };

  const retryFacets = () => {
    setFacetError("none");
    void loadFocusFacets();
    void resolveSelections();
  };

  return {
    lanes,
    observedMinutes,
    facetError,
    selected: {
      requiredIngredient: selectedFor(params.requiredIngredient, resolution?.ingredients ?? null),
      availableIngredient: selectedFor(params.availableIngredient, resolution?.ingredients ?? null),
      requiredTag: selectedFor(params.requiredTag, resolution?.tags ?? null),
      preferredTag: selectedFor(params.preferredTag, resolution?.tags ?? null),
    },
    searchLane,
    loadMore,
    retryFacets,
  };
}
