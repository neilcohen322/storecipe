jest.mock("react-native/Libraries/Utilities/useWindowDimensions", () => ({
  __esModule: true,
  default: jest.fn(() => ({ width: 768, height: 800, scale: 1, fontScale: 1 })),
}));

const mockCreateRecipeScreen = jest.fn((_props: unknown) => null);

jest.mock("expo-router", () => ({
  useRouter: () => ({ back: jest.fn(), replace: jest.fn() }),
}));

jest.mock("../../components/AppShell", () => ({
  getLayoutMode: () => "medium",
}));

jest.mock("../../api/ApiProvider", () => ({
  useApi: () => ({ client: {} }),
}));

jest.mock("../../auth/AuthProvider", () => ({
  useAuth: () => ({
    errorMessage: null,
    isAuthenticated: true,
    isLoading: false,
    login: jest.fn(),
    logout: jest.fn(),
  }),
}));

jest.mock("../../auth/AuthGate", () => ({
  AuthGate: ({ children }: { children: React.ReactNode }) => children,
}));

jest.mock("../../api/catalog", () => ({
  createCatalogApi: jest.fn(() => ({ createRecipe: jest.fn() })),
}));

jest.mock("../../api/ingestion", () => ({
  createIngestionApi: jest.fn(() => ({ normalizeIngredients: jest.fn() })),
}));

jest.mock("../../screens/CreateRecipeScreen", () => ({
  CreateRecipeScreen: (props: unknown) => mockCreateRecipeScreen(props),
}));

import { render } from "@testing-library/react-native";
import { createCatalogApi } from "../../api/catalog";
import { createIngestionApi } from "../../api/ingestion";
import { NewRecipeRouteAdapter } from "../LegacyRouteAdapters";

const mockedCreateCatalogApi = createCatalogApi as jest.MockedFunction<typeof createCatalogApi>;
const mockedCreateIngestionApi = createIngestionApi as jest.MockedFunction<typeof createIngestionApi>;

beforeEach(() => {
  jest.clearAllMocks();
});

test("NewRecipeRouteAdapter passes catalog and ingestion clients to CreateRecipeScreen", async () => {
  await render(<NewRecipeRouteAdapter />);
  expect(mockedCreateCatalogApi).toHaveBeenCalled();
  expect(mockedCreateIngestionApi).toHaveBeenCalled();
  expect(mockCreateRecipeScreen).toHaveBeenCalledWith(
    expect.objectContaining({
      catalog: mockedCreateCatalogApi.mock.results[0]?.value,
      ingestion: mockedCreateIngestionApi.mock.results[0]?.value,
      layoutMode: "medium",
    }),
  );
});
