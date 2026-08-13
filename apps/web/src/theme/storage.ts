import type AsyncStorageModule from "@react-native-async-storage/async-storage";
import { Platform } from "react-native";

export const THEME_STORAGE_KEY = "storecipe.theme";

export type ThemeStorage = {
  platform: "web" | "native";
  getSync?: (key: string) => string | null;
  get: (key: string) => Promise<string | null>;
  set: (key: string, value: string) => Promise<void>;
};

function readWebStorage(key: string): string | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function createThemeStorage(): ThemeStorage {
  if (Platform.OS === "web") {
    return {
      platform: "web",
      getSync: readWebStorage,
      get: async (key) => readWebStorage(key),
      set: async (key, value) => {
        try {
          window.localStorage.setItem(key, value);
        } catch {
          // Theme persistence is best-effort when browser storage is unavailable.
        }
      },
    };
  }

  return {
    platform: "native",
    get: (key) => {
      const AsyncStorage = require("@react-native-async-storage/async-storage")
        .default as typeof AsyncStorageModule;
      return AsyncStorage.getItem(key);
    },
    set: (key, value) => {
      const AsyncStorage = require("@react-native-async-storage/async-storage")
        .default as typeof AsyncStorageModule;
      return AsyncStorage.setItem(key, value);
    },
  };
}
