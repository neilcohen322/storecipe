import { Text } from "react-native";
import { render, waitFor } from "@testing-library/react-native";

import { useReducedMotion } from "../useReducedMotion";

function Probe() {
  const reduced = useReducedMotion();
  return <Text>{reduced ? "reduced" : "full"}</Text>;
}

test("defaults to reduced motion until the platform reports a preference", async () => {
  const { AccessibilityInfo } = jest.requireActual("react-native") as typeof import("react-native");
  const spy = jest.spyOn(AccessibilityInfo, "isReduceMotionEnabled").mockReturnValue(new Promise(() => undefined));
  const add = jest.spyOn(AccessibilityInfo, "addEventListener").mockReturnValue({ remove: jest.fn() } as never);
  const originalMatchMedia = window.matchMedia;
  Object.defineProperty(window, "matchMedia", { configurable: true, value: undefined });
  try {
    const screen = await render(<Probe />);
    expect(screen.getByText("reduced")).toBeTruthy();
  } finally {
    spy.mockRestore();
    add.mockRestore();
    Object.defineProperty(window, "matchMedia", { configurable: true, value: originalMatchMedia });
  }
});

test("follows AccessibilityInfo reduce-motion", async () => {
  const { AccessibilityInfo } = jest.requireActual("react-native") as typeof import("react-native");
  const spy = jest.spyOn(AccessibilityInfo, "isReduceMotionEnabled").mockResolvedValue(true);
  const add = jest.spyOn(AccessibilityInfo, "addEventListener").mockReturnValue({ remove: jest.fn() } as never);
  try {
    const screen = await render(<Probe />);
    await waitFor(() => expect(screen.getByText("reduced")).toBeTruthy());
  } finally {
    spy.mockRestore();
    add.mockRestore();
  }
});
