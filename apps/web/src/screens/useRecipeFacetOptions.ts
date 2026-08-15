import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, ApiUnauthorizedError } from "../api/client";
import type {
  createCatalogApi,
  RecipeFacetBrowseParams,
  RecipeFacetPage,
} from "../api/catalog";

export type LaneId = "ingredient" | "tag";

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

const ALL_LANES: LaneId[] = ["ingredient", "tag"];

function emptyLane(): LaneState {
  return { search: "", options: [], nextCursor: null, loading: false, loadingMore: false };
}

function emptyLanes(): Record<LaneId, LaneState> {
  return {
    ingredient: emptyLane(),
    tag: emptyLane(),
  };
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

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function browseParamsForLane(id: LaneId, search: string, cursor: string | null): RecipeFacetBrowseParams {
  if (id === "ingredient") {
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
  if (id === "ingredient") {
    return { names: page.ingredients, nextCursor: page.ingredientNextCursor };
  }
  return { names: page.tags, nextCursor: page.tagNextCursor };
}

function retainMovedLanes(
  current: Record<LaneId, LaneState>,
  started: Record<LaneId, number>,
  currentGenerations: Record<LaneId, number>,
  fallback: (id: LaneId) => LaneState,
): Record<LaneId, LaneState> {
  const keep = (id: LaneId) => (currentGenerations[id] !== started[id] ? current[id] : fallback(id));
  return {
    ingredient: keep("ingredient"),
    tag: keep("tag"),
  };
}

function snapshotLaneGenerations(runtimes: Record<LaneId, LaneRuntime>): Record<LaneId, number> {
  return {
    ingredient: runtimes.ingredient.generation,
    tag: runtimes.tag.generation,
  };
}

export type UseRecipeFacetOptionsArgs = {
  catalog: ReturnType<typeof createCatalogApi>;
  onUnauthorized: () => void;
};

export function useRecipeFacetOptions({ catalog, onUnauthorized }: UseRecipeFacetOptionsArgs) {
  const [lanes, setLanes] = useState<Record<LaneId, LaneState>>(emptyLanes);
  const [observedMinutes, setObservedMinutes] = useState<{ min: number; max: number } | null>(null);
  const [facetError, setFacetError] = useState<"none" | "generic">("none");

  const catalogRef = useRef(catalog);
  catalogRef.current = catalog;
  const onUnauthorizedRef = useRef(onUnauthorized);
  onUnauthorizedRef.current = onUnauthorized;

  const runtimes = useRef<Record<LaneId, LaneRuntime>>({
    ingredient: { generation: 0, controller: null, debounce: null },
    tag: { generation: 0, controller: null, debounce: null },
  });
  const focusRuntime = useRef({ generation: 0, controller: null as AbortController | null });
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
      patchLane(id, { loading: true, loadingMore: false, nextCursor: null });
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
    const startedLanes = snapshotLaneGenerations(runtimes.current);
    setLanes({
      ingredient: { ...emptyLane(), loading: true },
      tag: { ...emptyLane(), loading: true },
    });
    setFacetError("none");
    try {
      const page = await catalogRef.current.listRecipeFacets({}, { signal: controller.signal });
      if (!mounted.current || generation !== focusRuntime.current.generation) return;
      setObservedMinutes(page.totalMinutes);
      setLanes((current) => retainMovedLanes(
        current,
        startedLanes,
        snapshotLaneGenerations(runtimes.current),
        (id) => {
          const { names, nextCursor } = pageNames(id, page);
          return { search: "", options: names, nextCursor, loading: false, loadingMore: false };
        },
      ));
    } catch (error) {
      if (!mounted.current || generation !== focusRuntime.current.generation || isAbortError(error)) return;
      if (handleUnauthorized(error)) {
        setLanes(emptyLanes());
        return;
      }
      setFacetError("generic");
      setLanes((current) => retainMovedLanes(
        current,
        startedLanes,
        snapshotLaneGenerations(runtimes.current),
        () => emptyLane(),
      ));
    }
  }, []);

  const searchLane = (id: LaneId, value: string) => {
    const runtime = runtimes.current[id];
    runtime.controller?.abort();
    runtime.generation += 1;
    patchLane(id, { search: value, nextCursor: null });
    if (runtime.debounce) clearTimeout(runtime.debounce);
    runtime.debounce = setTimeout(() => {
      runtime.debounce = null;
      void fetchLane(id, value, null, "replace");
    }, 500);
  };

  const loadMore = (id: LaneId) => {
    const lane = lanes[id];
    if (!lane.nextCursor || lane.loadingMore || lane.loading || runtimes.current[id].debounce) return;
    void fetchLane(id, lane.search, lane.nextCursor, "append");
  };

  const retryFacets = () => {
    setFacetError("none");
    void loadFocusFacets();
  };

  return {
    lanes,
    observedMinutes,
    facetError,
    searchLane,
    loadMore,
    retryFacets,
    browse: loadFocusFacets,
  };
}
