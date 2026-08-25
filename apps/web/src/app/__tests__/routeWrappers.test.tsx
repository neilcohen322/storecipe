import { fireEvent, render, waitFor } from "@testing-library/react-native";

const mockLogin = jest.fn();
const mockLogout = jest.fn();
const mockReplace = jest.fn();
const mockRequestAccountDeletion = jest.fn();
let mockRouteParams: { recipeId?: string | string[] } = { recipeId: "recipe-1" };

jest.mock("expo-router", () => ({
  Link: () => null,
  useLocalSearchParams: () => mockRouteParams,
  usePathname: () => "/recipes",
  useRouter: () => ({
    back: jest.fn(),
    push: jest.fn(),
    replace: mockReplace,
  }),
}));

jest.mock("react-native-auth0", () => ({
  Auth0Provider: ({ children }: { children: React.ReactNode }) => children,
  useAuth0: jest.fn(),
}));

jest.mock("../../api/ApiProvider", () => ({
  useApi: () => ({ client: {}, accountDeletionClient: {} }),
}));

jest.mock("../../api/catalog", () => ({
  createCatalogApi: () => ({ requestAccountDeletion: mockRequestAccountDeletion }),
}));

jest.mock("../../auth/AuthProvider", () => ({
  useAuth: () => ({
    errorMessage: null,
    isAuthenticated: true,
    isLoading: false,
    login: mockLogin,
    logout: mockLogout,
  }),
}));

jest.mock("../../screens/RecipeListScreen", () => {
  const { Pressable, Text } = require("react-native");
  return {
    RecipeListScreen: ({ onUnauthorized }: { onUnauthorized(): void }) => (
      <Pressable testID="unauthorized" onPress={onUnauthorized}>
        <Text>Unauthorized</Text>
      </Pressable>
    ),
  };
});

jest.mock("../../screens/RecipeDetailScreen", () => {
  const { Text } = require("react-native");
  return { RecipeDetailScreen: ({ recipeId }: { recipeId: unknown }) => <Text testID="detail-route-id">{Array.isArray(recipeId) ? recipeId.join(",") : recipeId ?? "missing"}</Text> };
});

jest.mock("../../screens/CookingScreen", () => {
  const { Text } = require("react-native");
  return { CookingScreen: ({ recipeId }: { recipeId: unknown }) => <Text testID="cook-route-id">{Array.isArray(recipeId) ? recipeId.join(",") : recipeId ?? "missing"}</Text> };
});

jest.mock("../../screens/AccountScreen", () => {
  const { Pressable, Text } = require("react-native");
  return {
    AccountScreen: ({ onDeleteAccount }: { onDeleteAccount(): Promise<void> }) => (
      <Pressable accessibilityRole="button" accessibilityLabel="Delete my account" onPress={() => void onDeleteAccount()}><Text>Delete my account</Text></Pressable>
    ),
  };
});

import AccountRoute from "../../../app/(app)/account";
import ImportsRoute from "../../../app/(app)/imports";
import NewImportRoute from "../../../app/(app)/imports/new";
import MoreRoute from "../../../app/(app)/more";
import NewRecipeRoute from "../../../app/(app)/recipes/new";
import RecipeDetailRoute from "../../../app/(app)/recipes/[recipeId]/index";
import CookingRoute from "../../../app/(app)/recipes/[recipeId]/cook";
import RecipesRoute from "../../../app/(app)/recipes";
import IndexRoute from "../../../app/index";
import PrivacyRoute from "../../../app/privacy";
import TermsRoute from "../../../app/terms";

const routes = [
  ["/", IndexRoute],
  ["/recipes", RecipesRoute],
  ["/recipes/new", NewRecipeRoute],
  ["/recipes/:recipeId", RecipeDetailRoute],
  ["/recipes/:recipeId/cook", CookingRoute],
  ["/imports", ImportsRoute],
  ["/imports/new", NewImportRoute],
  ["/account", AccountRoute],
  ["/more", MoreRoute],
  ["/privacy", PrivacyRoute],
  ["/terms", TermsRoute],
] as const;

it.each(routes)("exports a route wrapper for %s", (_path, Route) => {
  expect(Route).toEqual(expect.any(Function));
});

it("signs out after the account deletion endpoint accepts the request", async () => {
  mockRequestAccountDeletion.mockClear();
  mockLogout.mockClear();
  mockReplace.mockClear();
  mockRequestAccountDeletion.mockResolvedValue(undefined);
  mockLogout.mockResolvedValue(undefined);
  const screen = await render(<AccountRoute />);

  fireEvent.press(screen.getByRole("button", { name: "Delete my account" }));

  await waitFor(() => expect(mockReplace).toHaveBeenCalledWith("/"));
  expect(mockRequestAccountDeletion).toHaveBeenCalledTimes(1);
  expect(mockLogout).toHaveBeenCalledTimes(1);
});

it("preserves the authenticated session and returns to landing for unauthorized recipe requests", async () => {
  mockLogout.mockClear();
  mockLogin.mockClear();
  mockReplace.mockClear();
  mockLogin.mockResolvedValue(undefined);
  mockLogout.mockResolvedValue(undefined);

  const { getByTestId } = await render(<RecipesRoute />);
  fireEvent.press(getByTestId("unauthorized"));

  expect(mockLogout).not.toHaveBeenCalled();
  expect(mockLogin).not.toHaveBeenCalled();
  expect(mockReplace).toHaveBeenCalledWith("/");
});

it.each([
  [undefined, "missing"],
  [["recipe-1", "duplicate"], "recipe-1,duplicate"],
  ["recipe-1", "recipe-1"],
])("forwards the runtime recipeId parameter shape through the detail route wrapper", async (recipeId, expected) => {
  mockRouteParams = recipeId === undefined ? {} : { recipeId };
  const { getByTestId, unmount } = await render(<RecipeDetailRoute />);
  expect(getByTestId("detail-route-id").props.children).toBe(expected);
  await unmount();
});
