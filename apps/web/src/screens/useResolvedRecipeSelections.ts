import { useCallback, useEffect, useRef, useState } from "react";
import { useFocusEffect } from "expo-router";

import { ApiUnauthorizedError } from "../api/client";
import type {
  createCatalogApi,
  ListRecipesParams,
  RecipeFacetSelectionsResponse,
} from "../api/catalog";
import type { FacetPickerSelection } from "../components/FacetPicker";
import { applyCanonicalSelections, sameStringList } from "./recipeListParams";

function uniqueNames(values: string[]): string[] {
  return [...new Set(values)];
}

function ingredientTagKey(params: ListRecipesParams): string {
  return JSON.stringify({
    ingredient: params.ingredient ?? [],
    tag: params.tag ?? [],
  });
}

function bucketsDiffer(current: ListRecipesParams, next: ListRecipesParams): boolean {
  return (
    !sameStringList(current.ingredient ?? [], next.ingredient ?? [])
    || !sameStringList(current.tag ?? [], next.tag ?? [])
  );
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
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

export type UseResolvedRecipeSelectionsArgs = {
  catalog: ReturnType<typeof createCatalogApi>;
  params: ListRecipesParams;
  replaceFilters: (next: ListRecipesParams) => void;
  onUnauthorized: () => void;
};

export function useResolvedRecipeSelections({
  catalog,
  params,
  replaceFilters,
  onUnauthorized,
}: UseResolvedRecipeSelectionsArgs) {
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

  const resolveRuntime = useRef({ generation: 0, controller: null as AbortController | null });
  const previousKey = useRef(ingredientTagKey(params));
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      resolveRuntime.current.controller?.abort();
    };
  }, []);

  const handleUnauthorized = (error: unknown): boolean => {
    if (error instanceof ApiUnauthorizedError) {
      onUnauthorizedRef.current();
      return true;
    }
    return false;
  };

  const resolveSelections = useCallback(async () => {
    const requestKey = ingredientTagKey(paramsRef.current);
    const runtime = resolveRuntime.current;
    runtime.controller?.abort();
    const generation = ++runtime.generation;
    const controller = new AbortController();
    runtime.controller = controller;
    setResolution(null);
    const current = paramsRef.current;
    try {
      const result = await catalogRef.current.resolveRecipeFacetSelections({
        ingredients: uniqueNames(current.ingredient ?? []),
        tags: uniqueNames(current.tag ?? []),
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
      setResolution(null);
      setFacetError("generic");
    }
  }, []);

  useFocusEffect(useCallback(() => {
    void resolveSelections();
  }, [resolveSelections]));

  const currentKey = ingredientTagKey(params);
  useEffect(() => {
    if (previousKey.current === currentKey) return;
    previousKey.current = currentKey;
    setResolution(null);
    void resolveSelections();
  }, [currentKey, resolveSelections]);

  const retrySelections = () => {
    setFacetError("none");
    void resolveSelections();
  };

  return {
    selected: {
      ingredient: selectedFor(params.ingredient, resolution?.ingredients ?? null),
      tag: selectedFor(params.tag, resolution?.tags ?? null),
    },
    facetError,
    retrySelections,
  };
}
