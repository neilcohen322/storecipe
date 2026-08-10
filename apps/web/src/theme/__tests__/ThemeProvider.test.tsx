import { act, render, renderHook } from "@testing-library/react-native";
import { Text } from "react-native";

import { ThemeProvider, useTheme } from "../ThemeProvider";
import type { ThemeStorage } from "../storage";

function createWebStorage(value: string | null): ThemeStorage {
  return {
    platform: "web",
    getSync: jest.fn(() => value),
    get: jest.fn(async () => value),
    set: jest.fn(async () => undefined),
  };
}

function wrapper(storage: ThemeStorage, systemScheme: "light" | "dark" | null = "dark") {
  return function ThemeTestProvider({ children }: { children: React.ReactNode }) {
    return <ThemeProvider storage={storage} systemSchemeOverride={systemScheme}>{children}</ThemeProvider>;
  };
}

test("uses the system scheme on the first web render when no preference is stored", async () => {
  const storage = createWebStorage(null);
  const { result } = await renderHook(() => useTheme(), { wrapper: wrapper(storage) });

  expect(storage.getSync).toHaveBeenCalledWith("storecipe.theme");
  expect(result.current.preference).toBe("system");
  expect(result.current.resolvedScheme).toBe("dark");
  expect(result.current.theme.colors.canvas).toBe("#10231c");
});

test("uses a stored explicit preference on the first web render", async () => {
  const { result } = await renderHook(() => useTheme(), { wrapper: wrapper(createWebStorage("light")) });

  expect(result.current.preference).toBe("light");
  expect(result.current.resolvedScheme).toBe("light");
  expect(result.current.theme.colors.canvas).toBe("#f7fff9");
});

test("persists an explicit preference change", async () => {
  const storage = createWebStorage(null);
  const { result } = await renderHook(() => useTheme(), { wrapper: wrapper(storage) });

  await act(async () => {
    await result.current.setPreference("light");
  });

  expect(result.current.preference).toBe("light");
  expect(result.current.resolvedScheme).toBe("light");
  expect(storage.set).toHaveBeenCalledWith("storecipe.theme", "light");
});

test("tracks operating-system changes while preference is system", async () => {
  const storage = createWebStorage("system");
  let systemScheme: "light" | "dark" | null = "dark";
  const { result, rerender } = await renderHook(() => useTheme(), {
    wrapper: ({ children }) => <ThemeProvider storage={storage} systemSchemeOverride={systemScheme}>{children}</ThemeProvider>,
  });

  expect(result.current.resolvedScheme).toBe("dark");
  systemScheme = "light";
  await rerender({});
  expect(result.current.resolvedScheme).toBe("light");
});

test("falls back to system mode for an invalid stored preference on the first web render", async () => {
  const { result } = await renderHook(() => useTheme(), { wrapper: wrapper(createWebStorage("sepia")) });

  expect(result.current.preference).toBe("system");
  expect(result.current.resolvedScheme).toBe("dark");
  expect(result.current.theme.colors.canvas).toBe("#10231c");
});

test("keeps native descendants behind the loading surface until storage hydration finishes", async () => {
  let resolveStoredPreference: (value: string | null) => void = () => undefined;
  const storage: ThemeStorage = {
    platform: "native",
    get: jest.fn(() => new Promise((resolve) => { resolveStoredPreference = resolve; })),
    set: jest.fn(async () => undefined),
  };

  const { getByTestId, queryByText } = await render(
    <ThemeProvider storage={storage}><Text>themed child</Text></ThemeProvider>,
  );

  expect(getByTestId("theme-loading")).toBeOnTheScreen();
  expect(queryByText("themed child")).toBeNull();

  await act(async () => { resolveStoredPreference("light"); });
  expect(queryByText("themed child")).toBeOnTheScreen();
});
