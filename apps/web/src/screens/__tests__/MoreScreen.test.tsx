import { fireEvent, render } from "@testing-library/react-native";

import { moreItems } from "../../navigation/registry";
import { MoreScreen } from "../MoreScreen";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../components/ThemeControl", () => ({ ThemeControl: () => null }));
jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

test("renders every registry overflow entry without copying navigation labels", async () => {
  const screen = await render(<MoreScreen onNavigate={jest.fn()} onLogout={jest.fn()} />);

  for (const item of moreItems("compact")) {
    expect(screen.getByText(item.label)).toBeTruthy();
  }
  await screen.unmount();
});

test("keeps overflow entries reachable in a vertical scroll container", async () => {
  const screen = await render(<MoreScreen onNavigate={jest.fn()} onLogout={jest.fn()} />);

  const scrollView = screen.getByTestId("more-scroll-view");
  expect(scrollView.props.contentContainerStyle).toBeTruthy();
  await screen.unmount();
});

test("navigates overflow links and runs action controls", async () => {
  const onNavigate = jest.fn();
  const onLogout = jest.fn().mockResolvedValue(undefined);
  const screen = await render(<MoreScreen onNavigate={onNavigate} onLogout={onLogout} />);

  await fireEvent.press(screen.getByRole("link", { name: "Account" }));
  expect(onLogout).not.toHaveBeenCalled();
  await fireEvent.press(screen.getByRole("button", { name: "Logout" }));

  expect(onNavigate).toHaveBeenCalledWith("/account");
  expect(onLogout).toHaveBeenCalledTimes(1);
  await screen.unmount();
});
