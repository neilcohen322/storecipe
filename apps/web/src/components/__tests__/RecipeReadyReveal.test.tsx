import { AccessibilityInfo } from "react-native";
import { render } from "@testing-library/react-native";

import { RecipeReadyReveal } from "../RecipeReadyReveal";

jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

test("hides until a first successful import completion", async () => {
  const hidden = await render(<RecipeReadyReveal visible={false} />);
  expect(hidden.queryByTestId("recipe-ready-reveal")).toBeNull();
});

test("announces Recipe ready without confetti copy", async () => {
  jest.spyOn(AccessibilityInfo, "isReduceMotionEnabled").mockResolvedValue(true);
  jest.spyOn(AccessibilityInfo, "addEventListener").mockReturnValue({ remove: jest.fn() } as never);
  const screen = await render(<RecipeReadyReveal visible />);
  expect(screen.getByTestId("recipe-ready-reveal")).toBeTruthy();
  expect(screen.getByRole("header", { name: "Recipe ready" })).toBeTruthy();
  expect(screen.getByText("Your recipe import is complete.")).toBeTruthy();
  expect(screen.queryByText(/confetti/i)).toBeNull();
});
