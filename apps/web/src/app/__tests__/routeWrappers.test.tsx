jest.mock("react-native-auth0", () => ({
  Auth0Provider: ({ children }: { children: React.ReactNode }) => children,
  useAuth0: jest.fn(),
}));

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
