import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("../../theme/ThemeProvider", () => ({
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
}));

import { ServingStepper } from "../ServingStepper";

test("increases and decreases a servings stepper within 1 to 99", async () => {
  const onChange = jest.fn();
  const screen = await render(
    <ServingStepper servings={4} value={{ kind: "servings", servings: 4 }} onChange={onChange} />,
  );
  expect(screen.getByText("4")).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Increase servings" }));
  expect(onChange).toHaveBeenCalledWith({ kind: "servings", servings: 5 });
  await fireEvent.press(screen.getByRole("button", { name: "Decrease servings" }));
  expect(onChange).toHaveBeenCalledWith({ kind: "servings", servings: 3 });
});

test("disables servings buttons at the bounds", async () => {
  const min = await render(
    <ServingStepper servings={4} value={{ kind: "servings", servings: 1 }} onChange={jest.fn()} />,
  );
  expect(min.getByRole("button", { name: "Decrease servings" }).props.accessibilityState).toMatchObject({ disabled: true });
  const max = await render(
    <ServingStepper servings={4} value={{ kind: "servings", servings: 99 }} onChange={jest.fn()} />,
  );
  expect(max.getByRole("button", { name: "Increase servings" }).props.accessibilityState).toMatchObject({ disabled: true });
});

test("offers half, one, and double multipliers when servings are unknown", async () => {
  const onChange = jest.fn();
  const screen = await render(
    <ServingStepper servings={null} value={{ kind: "multiplier", multiplier: 1 }} onChange={onChange} />,
  );
  expect(screen.getByRole("button", { name: "1×" }).props.accessibilityState?.selected).toBe(true);
  await fireEvent.press(screen.getByRole("button", { name: "½×" }));
  expect(onChange).toHaveBeenCalledWith({ kind: "multiplier", multiplier: 0.5 });
  await fireEvent.press(screen.getByRole("button", { name: "2×" }));
  expect(onChange).toHaveBeenCalledWith({ kind: "multiplier", multiplier: 2 });
});
