import * as ImagePicker from "expo-image-picker";

import { pickRecipeCoverImage } from "../imagePicker";

jest.mock("expo-image-picker", () => ({
  launchImageLibraryAsync: jest.fn(),
}));

const launch = ImagePicker.launchImageLibraryAsync as jest.Mock;

test("returns cancelled when the picker is dismissed", async () => {
  launch.mockResolvedValue({ canceled: true, assets: [] });
  await expect(pickRecipeCoverImage()).resolves.toEqual({ status: "cancelled" });
  expect(launch).toHaveBeenCalledWith({
    mediaTypes: ["images"],
    allowsMultipleSelection: false,
    exif: false,
  });
});

test("accepts jpeg png and webp", async () => {
  for (const mimeType of ["image/jpeg", "image/png", "image/webp"]) {
    launch.mockResolvedValue({
      canceled: false,
      assets: [{ uri: "blob:cover", mimeType, fileName: "cover", fileSize: 12 }],
    });
    await expect(pickRecipeCoverImage()).resolves.toMatchObject({ status: "selected", mimeType });
  }
});

test("rejects unsupported MIME types", async () => {
  launch.mockResolvedValue({
    canceled: false,
    assets: [{ uri: "blob:cover", mimeType: "image/heic", fileSize: 12 }],
  });
  await expect(pickRecipeCoverImage()).resolves.toEqual({ status: "unsupported" });
});

test("rejects files over 8 MiB", async () => {
  launch.mockResolvedValue({
    canceled: false,
    assets: [{ uri: "blob:cover", mimeType: "image/jpeg", fileSize: 8 * 1024 * 1024 + 1 }],
  });
  await expect(pickRecipeCoverImage()).resolves.toEqual({ status: "too_large" });
});
