import { Platform } from "react-native";

export const COOKING_SESSION_KEY_PREFIX = "storecipe.cooking.v1:";

export type CookingSessionScale =
  | { kind: "servings"; servings: number }
  | { kind: "multiplier"; multiplier: 0.5 | 1 | 2 };

export type CookingTimerState = {
  instructionIndex: number;
  durationSeconds: number;
  status: "running" | "paused" | "elapsed";
  deadlineMs: number | null;
  remainingSeconds: number | null;
};

export type CookingSession = {
  version: 1;
  recipeId: string;
  stepIndex: number;
  checkedIngredientKeys: string[];
  scale: CookingSessionScale;
  keepAwake: boolean;
  timer: CookingTimerState | null;
};

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem" | "key" | "length">;

function sessionKey(recipeId: string): string {
  return `${COOKING_SESSION_KEY_PREFIX}${recipeId}`;
}

function webLocalStorage(): StorageLike | null {
  if (Platform.OS !== "web" || typeof window === "undefined") {
    return null;
  }
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function isScale(value: unknown): value is CookingSessionScale {
  if (!value || typeof value !== "object") {
    return false;
  }
  const scale = value as CookingSessionScale;
  if (scale.kind === "servings") {
    return typeof scale.servings === "number" && Number.isFinite(scale.servings);
  }
  if (scale.kind === "multiplier") {
    return scale.multiplier === 0.5 || scale.multiplier === 1 || scale.multiplier === 2;
  }
  return false;
}

function isTimerState(value: unknown): value is CookingTimerState {
  if (!value || typeof value !== "object") {
    return false;
  }
  const timer = value as CookingTimerState;
  if (
    typeof timer.instructionIndex !== "number" ||
    typeof timer.durationSeconds !== "number" ||
    (timer.status !== "running" && timer.status !== "paused" && timer.status !== "elapsed")
  ) {
    return false;
  }
  if (timer.deadlineMs !== null && typeof timer.deadlineMs !== "number") {
    return false;
  }
  if (timer.remainingSeconds !== null && typeof timer.remainingSeconds !== "number") {
    return false;
  }
  return true;
}

function parseCookingSession(raw: string, recipeId: string): CookingSession | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") {
    return null;
  }
  const session = parsed as Partial<CookingSession>;
  if (
    session.version !== 1 ||
    session.recipeId !== recipeId ||
    typeof session.stepIndex !== "number" ||
    !Array.isArray(session.checkedIngredientKeys) ||
    !session.checkedIngredientKeys.every((key) => typeof key === "string") ||
    !isScale(session.scale) ||
    typeof session.keepAwake !== "boolean" ||
    (session.timer !== null && session.timer !== undefined && !isTimerState(session.timer))
  ) {
    return null;
  }
  return {
    version: 1,
    recipeId,
    stepIndex: session.stepIndex,
    checkedIngredientKeys: session.checkedIngredientKeys,
    scale: session.scale,
    keepAwake: session.keepAwake,
    timer: session.timer ?? null,
  };
}

export function createDefaultSession(
  recipeId: string,
  scale: CookingSession["scale"],
): CookingSession {
  return {
    version: 1,
    recipeId,
    stepIndex: 0,
    checkedIngredientKeys: [],
    scale,
    keepAwake: false,
    timer: null,
  };
}

export function loadCookingSession(recipeId: string): CookingSession | null {
  const storage = webLocalStorage();
  if (!storage) {
    return null;
  }
  try {
    const raw = storage.getItem(sessionKey(recipeId));
    if (!raw) {
      return null;
    }
    return parseCookingSession(raw, recipeId);
  } catch {
    return null;
  }
}

export function saveCookingSession(session: CookingSession): void {
  const storage = webLocalStorage();
  if (!storage) {
    return;
  }
  try {
    storage.setItem(sessionKey(session.recipeId), JSON.stringify(session));
  } catch {
    // Ignore quota and privacy-mode failures.
  }
}

export function clearCookingSession(recipeId: string): void {
  const storage = webLocalStorage();
  if (!storage) {
    return;
  }
  try {
    storage.removeItem(sessionKey(recipeId));
  } catch {
    // Ignore storage failures.
  }
}

export function clearAllCookingSessions(storage: StorageLike | null = webLocalStorage()): void {
  if (!storage) {
    return;
  }
  try {
    const keysToRemove: string[] = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key?.startsWith(COOKING_SESSION_KEY_PREFIX)) {
        keysToRemove.push(key);
      }
    }
    for (const key of keysToRemove) {
      storage.removeItem(key);
    }
  } catch {
    // Ignore storage failures.
  }
}
