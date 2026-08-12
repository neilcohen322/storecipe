import { fireEvent, render } from "@testing-library/react-native";

import { moreItems } from "../../navigation/registry";
import { MoreScreen } from "../MoreScreen";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../components/ThemeControl", () => ({ ThemeControl: () => null }));
jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({
    theme: {
      colors: { canvas: "#fff", surface: "#fff", text: "#111", mutedText: "#555", border: "#ddd", danger: "#b42318" },
      spacing: { sm: 8, md: 16, lg: 24 },
      radii: { md: 12 },
      sizing: { icon: 24 },
      type: { caption: 12, heading: 28 },
    },
  }),
}));

test("renders every registry overflow entry without copying navigation labels", async () => {
  const screen = await render(<MoreScreen onNavigate={jest.fn()} onLogout={jest.fn()} />);

  for (const item of moreItems()) {
    expect(screen.getByText(item.label)).toBeTruthy();
  }
  await screen.unmount();
});

test("navigates overflow links and runs action controls", async () => {
  const onNavigate = jest.fn();
  const onLogout = jest.fn().mockResolvedValue(undefined);
  const screen = await render(<MoreScreen onNavigate={onNavigate} onLogout={onLogout} />);

  await fireEvent.press(screen.getByRole("link", { name: "Account" }));
  await fireEvent.press(screen.getByRole("button", { name: "Logout" }));

  expect(onNavigate).toHaveBeenCalledWith("/account");
  expect(onLogout).toHaveBeenCalledTimes(1);
  await screen.unmount();
});
