import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 10, right: 4, bottom: 12, left: 6 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
  useTheme: () => ({
    theme: {
      colors: {
        canvas: "#f7fff9",
        surface: "#fff",
        elevatedSurface: "#fff",
        text: "#10231c",
        mutedText: "#527060",
        border: "#d0e5d6",
        accent: "#2d6a4f",
        accentHover: "#1b4332",
        accentContrast: "#fff",
        success: "#2d6a4f",
        warning: "#b7791f",
        danger: "#b42318",
        focusRing: "#40916c",
        scrim: "rgba(0,0,0,.4)",
      },
      spacing: { xs: 4, sm: 8, md: 16, lg: 24, xl: 32, "2xl": 48 },
      sizing: { control: 44, icon: 24, touchTarget: 48 },
      radii: { sm: 8, md: 12, lg: 16, pill: 999 },
      type: { caption: 12, body: 15, subtitle: 18, heading: 28, display: 54 },
    },
  }),
}));

import { FacetPicker } from "../FacetPicker";

test("renders selected chips and observed option buttons", async () => {
  const onAdd = jest.fn();
  const onRemove = jest.fn();
  const onSearch = jest.fn();
  const onLoadMore = jest.fn();
  const screen = await render(
    <FacetPicker
      label="Required ingredients"
      selected={[{ name: "basil" }, { name: "ghost", unavailable: true }]}
      options={["basil", "tomato"]}
      search=""
      onSearch={onSearch}
      hasMore
      loadingMore={false}
      onLoadMore={onLoadMore}
      onAdd={onAdd}
      onRemove={onRemove}
    />,
  );
  expect(screen.getByLabelText("Required ingredients")).toBeTruthy();
  expect(screen.getByText("unavailable")).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "tomato" }));
  expect(onAdd).toHaveBeenCalledWith("tomato");
  await fireEvent.changeText(screen.getByLabelText("Required ingredients"), "ketchup");
  expect(onSearch).toHaveBeenCalledWith("ketchup");
  expect(onAdd).not.toHaveBeenCalledWith("ketchup");
  await fireEvent.press(screen.getByRole("button", { name: "Remove basil" }));
  expect(onRemove).toHaveBeenCalledWith("basil");
});

test("load more is available until the next cursor is null", async () => {
  const onLoadMore = jest.fn();
  const first = await render(
    <FacetPicker
      label="Required ingredients"
      selected={[]}
      options={["basil"]}
      search=""
      onSearch={jest.fn()}
      hasMore
      loadingMore={false}
      onLoadMore={onLoadMore}
      onAdd={jest.fn()}
      onRemove={jest.fn()}
    />,
  );
  await fireEvent.press(first.getByRole("button", { name: "Load more options" }));
  expect(onLoadMore).toHaveBeenCalledTimes(1);
  await first.rerender(
    <FacetPicker
      label="Required ingredients"
      selected={[]}
      options={["basil", "tomato"]}
      search=""
      onSearch={jest.fn()}
      hasMore={false}
      loadingMore={false}
      onLoadMore={onLoadMore}
      onAdd={jest.fn()}
      onRemove={jest.fn()}
    />,
  );
  expect(first.queryByRole("button", { name: "Load more options" })).toBeNull();
});
