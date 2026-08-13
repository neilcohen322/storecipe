import { render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({ useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }) }));

import type { createIngestionApi } from "../../api/ingestion";
import { ImportSessionProvider } from "../../imports/ImportSessionProvider";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { ImportHistoryScreen } from "../ImportHistoryScreen";

test("shows an honest empty state when no locally retained active import exists", async () => {
  const ingestion = { createUrlImport: jest.fn(), createTextImport: jest.fn(), getImport: jest.fn() } as unknown as ReturnType<typeof createIngestionApi>;
  const screen = await render(<ThemeProvider systemSchemeOverride="light"><ImportSessionProvider ingestion={ingestion} onUnauthorized={jest.fn()}><ImportHistoryScreen onNewImport={jest.fn()} /></ImportSessionProvider></ThemeProvider>);

  expect(screen.getByText("No active imports")).toBeTruthy();
  expect(screen.getByText("Imports started in this session appear here while they are active.")).toBeTruthy();
  expect(screen.queryByText(/recent imports/i)).toBeNull();
});
