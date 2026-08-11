import { Platform } from "react-native";

import { isApprovedAppPath } from "../navigation/registry";

const RETURN_PATH_KEY = "storecipe.return-path";

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export type ReturnPathStorage = {
  save(path: string): void;
  consume(): string | null;
  clear(): void;
};

function normalizeReturnPath(path: string | null): string | null {
  if (!path || !path.startsWith("/") || path.startsWith("//") || path.includes("\\") || path.includes("?") || path.includes("#")) return null;
  try {
    if (decodeURIComponent(path) !== path) return null;
  } catch {
    return null;
  }
  return isApprovedAppPath(path) ? path : null;
}

function webSessionStorage(): StorageLike | null {
  if (Platform.OS !== "web" || typeof window === "undefined") return null;
  try { return window.sessionStorage; } catch { return null; }
}

export function createReturnPathStorage(providedStorage?: StorageLike | null): ReturnPathStorage {
  const storage = providedStorage === undefined ? webSessionStorage() : providedStorage;
  let memoryPath: string | null = null;
  const read = () => storage ? storage.getItem(RETURN_PATH_KEY) : memoryPath;
  const write = (path: string) => { if (storage) storage.setItem(RETURN_PATH_KEY, path); else memoryPath = path; };
  const clear = () => { if (storage) storage.removeItem(RETURN_PATH_KEY); else memoryPath = null; };
  return {
    save(path) { const normalized = normalizeReturnPath(path); if (normalized) write(normalized); else clear(); },
    consume() { const saved = read(); clear(); return normalizeReturnPath(saved); },
    clear,
  };
}

export const returnPathStorage = createReturnPathStorage();
