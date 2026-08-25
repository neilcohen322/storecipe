import { useEffect, useState } from "react";
import { AccessibilityInfo, Platform } from "react-native";

export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(true);

  useEffect(() => {
    let active = true;
    const apply = (value: boolean) => {
      if (active) setReduced(value);
    };

    void AccessibilityInfo.isReduceMotionEnabled().then(apply);
    const subscription = AccessibilityInfo.addEventListener("reduceMotionChanged", apply);

    let media: MediaQueryList | undefined;
    const onMedia = (event: { matches: boolean }) => apply(event.matches);
    if (Platform.OS === "web" && typeof window !== "undefined" && typeof window.matchMedia === "function") {
      media = window.matchMedia("(prefers-reduced-motion: reduce)");
      apply(media.matches);
      if (typeof media.addEventListener === "function") {
        media.addEventListener("change", onMedia);
      } else if (typeof media.addListener === "function") {
        media.addListener(onMedia);
      }
    }

    return () => {
      active = false;
      subscription.remove();
      if (!media) return;
      if (typeof media.removeEventListener === "function") {
        media.removeEventListener("change", onMedia);
      } else if (typeof media.removeListener === "function") {
        media.removeListener(onMedia);
      }
    };
  }, []);

  return reduced;
}
