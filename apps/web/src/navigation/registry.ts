import type { NavigationAction, NavigationEntry, NavigationLink } from "./types";
import type { Availability } from "./types";

export const navigationRegistry: readonly NavigationEntry[] = [
  { kind: "link", id: "recipes", label: "Recipes", icon: "book-outline", href: "/recipes", group: "workspace", availability: "all", authorization: "authenticated", routeMatch: "prefix", mobilePlacement: "primary" },
  { kind: "link", id: "create", label: "Create", icon: "add-circle-outline", href: "/recipes/new", group: "workspace", availability: "all", authorization: "authenticated", routeMatch: "exact", mobilePlacement: "primary" },
  { kind: "link", id: "imports", label: "Imports", icon: "cloud-download-outline", href: "/imports", group: "workspace", availability: "all", authorization: "authenticated", routeMatch: "prefix", mobilePlacement: "primary" },
  { kind: "link", id: "account", label: "Account", icon: "person-circle-outline", href: "/account", group: "account", availability: "all", authorization: "authenticated", routeMatch: "exact", mobilePlacement: "overflow" },
  { kind: "link", id: "more", label: "More", icon: "ellipsis-horizontal-circle-outline", href: "/more", group: "account", availability: "compact", authorization: "authenticated", routeMatch: "exact", mobilePlacement: "primary" },
  { kind: "action", id: "theme", label: "Theme", icon: "contrast-outline", group: "account", availability: "all", authorization: "authenticated", actionId: "theme" },
  { kind: "action", id: "logout", label: "Logout", icon: "log-out-outline", group: "account", availability: "all", authorization: "authenticated", actionId: "logout" },
] as const;

export const linkItems = navigationRegistry.filter((item): item is NavigationLink => item.kind === "link");
export const actionItems = navigationRegistry.filter((item): item is NavigationAction => item.kind === "action");

export function mobilePrimaryItems(): NavigationLink[] {
  return linkItems.filter((item) => item.mobilePlacement === "primary");
}

export function moreItems(availability: Availability): NavigationEntry[] {
  return navigationRegistry.filter((item) =>
    (item.availability === "all" || item.availability === availability) &&
    (item.kind === "action" || item.mobilePlacement === "overflow"),
  );
}

export function desktopNavigationItems(): NavigationLink[] {
  return linkItems.filter((item) => item.id !== "create" && item.availability !== "compact");
}

/** Returns only normalized, concrete paths handled by the app's route wrappers. */
export function isApprovedAppPath(path: string): boolean {
  return path === "/" || path === "/recipes" || path === "/recipes/new" ||
    /^\/recipes\/[^/]+$/.test(path) || path === "/imports" ||
    path === "/imports/new" || path === "/account" || path === "/more";
}
