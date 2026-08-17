jest.mock("react-native/Libraries/Utilities/useWindowDimensions", () => ({
  __esModule: true,
  default: jest.fn(() => ({ width: 768, height: 800, scale: 1, fontScale: 1 })),
}));

const mockCreateRecipeScreen = jest.fn((_props: unknown) => null);
const mockImportScreen = jest.fn((_props: unknown) => null);
const mockBack = jest.fn();
const mockReplace = jest.fn();
const mockPush = jest.fn();
let mockImportJobId: string | undefined;

jest.mock("expo-router", () => ({
  useRouter: () => ({ back: mockBack, replace: mockReplace, push: mockPush }),
  useLocalSearchParams: () => (mockImportJobId ? { importJobId: mockImportJobId } : {}),
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

jest.mock("../../screens/ImportScreen", () => ({
  ImportScreen: (props: unknown) => mockImportScreen(props),
}));

import { render } from "@testing-library/react-native";
import { createCatalogApi } from "../../api/catalog";
import { createIngestionApi } from "../../api/ingestion";
import { NewImportRouteAdapter, NewRecipeRouteAdapter } from "../LegacyRouteAdapters";

const mockedCreateCatalogApi = createCatalogApi as jest.MockedFunction<typeof createCatalogApi>;
const mockedCreateIngestionApi = createIngestionApi as jest.MockedFunction<typeof createIngestionApi>;

beforeEach(() => {
  jest.clearAllMocks();
  mockImportJobId = undefined;
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

test("NewImportRouteAdapter sends back to the imports list", async () => {
  await render(<NewImportRouteAdapter />);
  const props = mockImportScreen.mock.calls[0]?.[0] as { onBack(): void; onContinueExtractedRecipe(jobId: string): void };
  props.onBack();
  expect(mockReplace).toHaveBeenCalledWith("/imports");
  expect(mockBack).not.toHaveBeenCalled();
});

test("NewImportRouteAdapter opens Create with the extracted import job", async () => {
  await render(<NewImportRouteAdapter />);
  const props = mockImportScreen.mock.calls[0]?.[0] as { onBack(): void; onContinueExtractedRecipe(jobId: string): void };
  props.onContinueExtractedRecipe("job-1");
  expect(mockPush).toHaveBeenCalledWith({ pathname: "/recipes/new", params: { importJobId: "job-1" } });
});

test("NewRecipeRouteAdapter forwards an import job id into Create recipe", async () => {
  mockImportJobId = "job-1";
  await render(<NewRecipeRouteAdapter />);
  expect(mockCreateRecipeScreen).toHaveBeenCalledWith(
    expect.objectContaining({ importJobId: "job-1" }),
  );
});
