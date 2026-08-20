import { getTheme } from "./tokens";
import type { Theme } from "./types";

export function createTestTheme(): Theme {
  return getTheme("light");
}
