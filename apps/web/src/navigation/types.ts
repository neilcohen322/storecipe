import type { Ionicons } from "@expo/vector-icons";
import type { Href } from "expo-router";

export type NavigationGroup = "workspace" | "account";
export type Availability = "all" | "desktop" | "compact";
export type AuthorizationRequirement = "authenticated";
export type RouteMatch = "exact" | "prefix";
export type MobilePlacement = "primary" | "overflow";
export type NavigationIcon = React.ComponentProps<typeof Ionicons>["name"];

type RegistryBase = {
  id: string;
  label: string;
  icon: NavigationIcon;
  group: NavigationGroup;
  availability: Availability;
  authorization: AuthorizationRequirement;
};

export type NavigationLink = RegistryBase & {
  kind: "link";
  href: Href;
  routeMatch: RouteMatch;
  mobilePlacement: MobilePlacement;
};

export type NavigationAction = RegistryBase & {
  kind: "action";
  actionId: "theme" | "logout";
};

export type NavigationEntry = NavigationLink | NavigationAction;
export type LayoutMode = "compact" | "medium" | "expanded";
