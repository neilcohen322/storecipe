import { render } from "@testing-library/react-native";

import type { createIngestionApi } from "../../api/ingestion";
import { ImportSessionProvider } from "../../imports/ImportSessionProvider";
import { ImportHistoryScreen } from "../ImportHistoryScreen";

test("shows an honest empty state when no locally retained active import exists", async () => {
  const ingestion = { createUrlImport: jest.fn(), createTextImport: jest.fn(), getImport: jest.fn() } as unknown as ReturnType<typeof createIngestionApi>;
  const screen = await render(<ImportSessionProvider ingestion={ingestion} onUnauthorized={jest.fn()}><ImportHistoryScreen onNewImport={jest.fn()} /></ImportSessionProvider>);

  expect(screen.getByText("No active imports")).toBeTruthy();
  expect(screen.getByText("Imports started in this session appear here while they are active.")).toBeTruthy();
  expect(screen.queryByText(/recent imports/i)).toBeNull();
});
