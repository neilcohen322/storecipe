import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 10, right: 4, bottom: 12, left: 6 }),
}));

jest.mock("../../theme/ThemeProvider", () => ({
  ThemeProvider: ({ children }: { children: React.ReactNode }) => children,
  useTheme: () => ({ theme: jest.requireActual("../../theme/testTheme").createTestTheme() }),
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

test("search stays editable while loading", async () => {
  const onSearch = jest.fn();
  const screen = await render(
    <FacetPicker
      label="Required ingredients"
      selected={[]}
      options={[]}
      search=""
      onSearch={onSearch}
      hasMore={false}
      loadingMore={false}
      onLoadMore={jest.fn()}
      loading
      onAdd={jest.fn()}
      onRemove={jest.fn()}
    />,
  );
  await fireEvent.changeText(screen.getByLabelText("Required ingredients"), "ketchup");
  expect(onSearch).toHaveBeenCalledWith("ketchup");
});

test("disables unselected options when additions are at the limit", async () => {
  const onAdd = jest.fn();
  const screen = await render(
    <FacetPicker
      label="Ingredients"
      selected={[{ name: "basil" }]}
      options={["basil", "tomato"]}
      search=""
      onSearch={jest.fn()}
      hasMore={false}
      loadingMore={false}
      onLoadMore={jest.fn()}
      addDisabled
      onAdd={onAdd}
      onRemove={jest.fn()}
    />,
  );
  const option = screen.getByRole("button", { name: "tomato" });
  expect(option.props.accessibilityState?.disabled).toBe(true);
  expect(option.props.focusable).toBe(false);
  await fireEvent.press(option);
  expect(onAdd).not.toHaveBeenCalled();
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
