import { Platform } from "react-native";

import {
  clearAllCookingSessions,
  clearCookingSession,
  COOKING_SESSION_KEY_PREFIX,
  createDefaultSession,
  loadCookingSession,
  saveCookingSession,
} from "../session";

type MemoryStorage = Storage;

function createMemoryStorage(): MemoryStorage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear() {
      store.clear();
    },
    getItem(key: string) {
      return store.has(key) ? store.get(key)! : null;
    },
    key(index: number) {
      return [...store.keys()][index] ?? null;
    },
    removeItem(key: string) {
      store.delete(key);
    },
    setItem(key: string, value: string) {
      store.set(key, value);
    },
  };
}

beforeEach(() => {
  jest.replaceProperty(Platform, "OS", "web");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { localStorage: createMemoryStorage() },
  });
});

afterEach(() => {
  jest.restoreAllMocks();
});

test("creates, saves, and loads a cooking session", () => {
  const session = createDefaultSession("recipe-1", { kind: "servings", servings: 4 });
  session.stepIndex = 2;
  session.checkedIngredientKeys = ["0:flour", "1:salt"];
  session.keepAwake = true;

  saveCookingSession(session);
  expect(loadCookingSession("recipe-1")).toEqual(session);
});

test("rejects malformed or mismatched sessions", () => {
  const storage = window.localStorage;
  storage.setItem(`${COOKING_SESSION_KEY_PREFIX}recipe-1`, "{not-json");
  expect(loadCookingSession("recipe-1")).toBeNull();

  storage.setItem(
    `${COOKING_SESSION_KEY_PREFIX}recipe-1`,
    JSON.stringify({ version: 2, recipeId: "recipe-1" }),
  );
  expect(loadCookingSession("recipe-1")).toBeNull();

  storage.setItem(
    `${COOKING_SESSION_KEY_PREFIX}recipe-1`,
    JSON.stringify({ version: 1, recipeId: "other-recipe", stepIndex: 0 }),
  );
  expect(loadCookingSession("recipe-1")).toBeNull();
});

test("clears one session and all prefixed sessions", () => {
  saveCookingSession(createDefaultSession("recipe-1", { kind: "multiplier", multiplier: 1 }));
  saveCookingSession(createDefaultSession("recipe-2", { kind: "multiplier", multiplier: 2 }));
  window.localStorage.setItem("storecipe.other", "keep");

  clearCookingSession("recipe-1");
  expect(loadCookingSession("recipe-1")).toBeNull();
  expect(loadCookingSession("recipe-2")).not.toBeNull();

  clearAllCookingSessions();
  expect(loadCookingSession("recipe-2")).toBeNull();
  expect(window.localStorage.getItem("storecipe.other")).toBe("keep");
});

test("persists and resumes a running timer deadline", () => {
  const session = createDefaultSession("recipe-1", { kind: "servings", servings: 2 });
  session.timer = {
    instructionIndex: 1,
    durationSeconds: 300,
    status: "running",
    deadlineMs: 1_700_000_000_000,
    remainingSeconds: null,
  };

  saveCookingSession(session);
  const loaded = loadCookingSession("recipe-1");
  expect(loaded?.timer).toEqual(session.timer);
});

test("returns null when localStorage is unavailable", () => {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: undefined,
  });
  expect(loadCookingSession("recipe-1")).toBeNull();
  expect(() => saveCookingSession(createDefaultSession("recipe-1", { kind: "multiplier", multiplier: 1 }))).not.toThrow();
});
