import "@testing-library/react-native";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

jest.mock("expo-image-picker", () => ({
  launchImageLibraryAsync: jest.fn(async () => ({ canceled: true, assets: [] })),
}));
