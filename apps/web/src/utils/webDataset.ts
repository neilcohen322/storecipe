import { Platform } from "react-native";

/** Maps to `data-*` attributes on React Native Web. No-ops on native. */
export function webDataset(attrs: Record<string, boolean | string>): object {
  if (Platform.OS !== "web") return {};
  return { dataSet: attrs };
}
