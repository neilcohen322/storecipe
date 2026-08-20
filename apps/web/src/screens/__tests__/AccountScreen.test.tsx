import { fireEvent, render } from "@testing-library/react-native";

import { AccountScreen } from "../AccountScreen";

jest.mock("../../components/ThemeControl", () => ({ ThemeControl: () => null }));
jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

test("shows Auth0 identity details and offers explicit logout", async () => {
  const onLogout = jest.fn().mockResolvedValue(undefined);
  const screen = await render(<AccountScreen identity={{ name: "Ada Lovelace", email: "ada@example.test" }} onLogout={onLogout} />);

  fireEvent.press(screen.getByRole("button", { name: "Log out" }));

  expect(screen.getByText("Ada Lovelace")).toBeTruthy();
  expect(screen.getByText("ada@example.test")).toBeTruthy();
  expect(onLogout).toHaveBeenCalledTimes(1);
});
