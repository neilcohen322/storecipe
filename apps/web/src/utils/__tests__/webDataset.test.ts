import { Platform } from "react-native";

import { webDataset } from "../webDataset";

test("emits dataSet only on web", () => {
  const original = Platform.OS;
  Object.defineProperty(Platform, "OS", { configurable: true, value: "web" });
  try {
    expect(webDataset({ printHide: true })).toEqual({ dataSet: { printHide: true } });
  } finally {
    Object.defineProperty(Platform, "OS", { configurable: true, value: original });
  }
});

test("is a no-op on native", () => {
  const original = Platform.OS;
  Object.defineProperty(Platform, "OS", { configurable: true, value: "ios" });
  try {
    expect(webDataset({ printHide: true })).toEqual({});
  } finally {
    Object.defineProperty(Platform, "OS", { configurable: true, value: original });
  }
});
