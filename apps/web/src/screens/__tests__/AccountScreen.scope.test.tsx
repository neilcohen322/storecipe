import { act, cleanup, fireEvent, render, waitFor } from "@testing-library/react-native";

import { AccountScreen } from "../AccountScreen";

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
const SCOPE_ERROR_COPY =
  "Sign out and back in to enable account deletion. Your current session does not include the required permission.";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((next, fail) => {
    resolve = next;
    reject = fail;
  });
  return { promise, resolve, reject };
}

test("shows the re-login guidance for an SDK scope error", async () => {
  const pending = deferred<void>();
  const error = Object.assign(new Error("scope"), { name: "MissingScopesError" });
  const onDeleteAccount = jest.fn().mockReturnValue(pending.promise);
  const screen = await render(<AccountScreen identity={null} onLogout={jest.fn()} onDeleteAccount={onDeleteAccount} />);
  const deleteButton = () => screen.getByRole("button", { name: "Delete my account" });

  fireEvent.changeText(screen.getByLabelText(CONFIRM_LABEL), CONFIRM_TEXT);
  await waitFor(() => expect(deleteButton().props.accessibilityState.disabled).toBe(false));
  await fireEvent.press(deleteButton());
  await waitFor(() => expect(onDeleteAccount).toHaveBeenCalledTimes(1));
  await act(async () => {
    pending.reject(error);
    await Promise.resolve();
  });
  expect(await screen.findByText(SCOPE_ERROR_COPY)).toBeTruthy();
  await waitFor(() => expect(deleteButton().props.accessibilityState.disabled).toBe(false));
});
