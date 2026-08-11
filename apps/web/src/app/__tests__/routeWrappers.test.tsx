import { fireEvent, render } from "@testing-library/react-native";

const mockLogin = jest.fn();
const mockLogout = jest.fn();
const mockReplace = jest.fn();

jest.mock("expo-router", () => ({
  Link: () => null,
  useLocalSearchParams: () => ({ recipeId: "recipe-1" }),
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
  useApi: () => ({ client: {} }),
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

import AccountRoute from "../../../app/(app)/account";
import ImportsRoute from "../../../app/(app)/imports";
import NewImportRoute from "../../../app/(app)/imports/new";
import MoreRoute from "../../../app/(app)/more";
import NewRecipeRoute from "../../../app/(app)/recipes/new";
import RecipeDetailRoute from "../../../app/(app)/recipes/[recipeId]";
import RecipesRoute from "../../../app/(app)/recipes";
import IndexRoute from "../../../app/index";

const routes = [
  ["/", IndexRoute],
  ["/recipes", RecipesRoute],
  ["/recipes/new", NewRecipeRoute],
  ["/recipes/:recipeId", RecipeDetailRoute],
  ["/imports", ImportsRoute],
  ["/imports/new", NewImportRoute],
  ["/account", AccountRoute],
  ["/more", MoreRoute],
] as const;

it.each(routes)("exports a route wrapper for %s", (_path, Route) => {
  expect(Route).toEqual(expect.any(Function));
});

it("clears a stale session and returns to landing for unauthorized recipe requests", async () => {
  mockLogin.mockResolvedValue(undefined);
  mockLogout.mockResolvedValue(undefined);

  const { getByTestId } = await render(<RecipesRoute />);
  fireEvent.press(getByTestId("unauthorized"));

  expect(mockLogout).toHaveBeenCalledTimes(1);
  expect(mockLogin).not.toHaveBeenCalled();
  expect(mockReplace).toHaveBeenCalledWith("/");
});
