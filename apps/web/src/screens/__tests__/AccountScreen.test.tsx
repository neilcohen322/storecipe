import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react-native";

import { AccountScreen, EXPORT_ERROR_COPY } from "../AccountScreen";

jest.mock("../../components/ThemeControl", () => ({ ThemeControl: () => null }));
jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));
jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, right: 0, bottom: 0, left: 0 }),
}));

afterEach(cleanup);

const CONFIRM_LABEL = "Type DELETE MY ACCOUNT to confirm";
const CONFIRM_TEXT = "DELETE MY ACCOUNT";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((next, fail) => {
    resolve = next;
    reject = fail;
  });
  return { promise, resolve, reject };
}

test("shows Auth0 identity details and offers explicit logout", async () => {
  const onLogout = jest.fn().mockResolvedValue(undefined);
  const screen = await render(
    <AccountScreen identity={{ name: "Ada Lovelace", email: "ada@example.test" }} onLogout={onLogout} onDeleteAccount={jest.fn()} />,
  );

  await fireEvent.press(screen.getByRole("button", { name: "Log out" }));

  expect(screen.getByText("Ada Lovelace")).toBeTruthy();
  expect(screen.getByText("ada@example.test")).toBeTruthy();
  expect(onLogout).toHaveBeenCalledTimes(1);
});

test("shows export error copy and retry label when export is enabled", async () => {
  const onExportCookbook = jest.fn().mockResolvedValue(undefined);
  const screen = await render(
    <AccountScreen
      identity={{ name: "Ada Lovelace", email: "ada@example.test" }}
      onLogout={jest.fn()}
      onDeleteAccount={jest.fn()}
      onExportCookbook={onExportCookbook}
      showExportCookbook
      exportError={EXPORT_ERROR_COPY}
    />,
  );

  expect(screen.getByText(EXPORT_ERROR_COPY)).toBeTruthy();
  expect(screen.getByText("Try export again")).toBeTruthy();
});

test("disables export while busy", async () => {
  const screen = await render(
    <AccountScreen
      identity={{ name: "Ada Lovelace", email: "ada@example.test" }}
      onLogout={jest.fn()}
      onDeleteAccount={jest.fn()}
      onExportCookbook={jest.fn()}
      showExportCookbook
      exportBusy
    />,
  );

  expect(screen.getByLabelText("Export cookbook").props.accessibilityState?.disabled).toBe(true);
});

test("calls onExportCookbook from the export button", async () => {
  const onExportCookbook = jest.fn().mockResolvedValue(undefined);
  const screen = await render(
    <AccountScreen
      identity={{ name: "Ada Lovelace", email: "ada@example.test" }}
      onLogout={jest.fn()}
      onDeleteAccount={jest.fn()}
      onExportCookbook={onExportCookbook}
      showExportCookbook
    />,
  );
  await fireEvent.press(screen.getByRole("button", { name: "Export cookbook" }));
  await waitFor(() => expect(onExportCookbook).toHaveBeenCalledTimes(1));
});

test("hides export controls when export is disabled", async () => {
  const screen = await render(
    <AccountScreen
      identity={{ name: "Ada Lovelace", email: "ada@example.test" }}
      onLogout={jest.fn()}
      onDeleteAccount={jest.fn()}
      onExportCookbook={jest.fn()}
      showExportCookbook={false}
    />,
  );
  expect(screen.queryByRole("button", { name: "Export cookbook" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Try export again" })).toBeNull();
});

test("requires the exact destructive confirmation and prevents duplicate deletion submits", async () => {
  const pending = deferred<void>();
  const onDeleteAccount = jest.fn().mockReturnValue(pending.promise);
  const screen = await render(<AccountScreen identity={null} onLogout={jest.fn()} onDeleteAccount={onDeleteAccount} />);
  const input = screen.getByLabelText(CONFIRM_LABEL);
  const deleteButton = () => screen.getByRole("button", { name: "Delete my account" });

  fireEvent.changeText(input, "delete my account");
  expect(deleteButton().props.accessibilityState.disabled).toBe(true);
  fireEvent.changeText(input, CONFIRM_TEXT);
  await waitFor(() => expect(deleteButton().props.accessibilityState.disabled).toBe(false));
  await fireEvent.press(deleteButton());
  await waitFor(() => expect(onDeleteAccount).toHaveBeenCalledTimes(1));
  await fireEvent.press(deleteButton());
  expect(onDeleteAccount).toHaveBeenCalledTimes(1);
  await act(async () => {
    pending.resolve();
    await pending.promise;
  });
  await waitFor(() => expect(deleteButton().props.accessibilityState.disabled).toBe(false));
});
