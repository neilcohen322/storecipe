import { ApiError, ApiNetworkError, ApiUnauthorizedError } from "../api/client";
import * as ImagePicker from "expo-image-picker";

const MAX_BYTES = 8 * 1024 * 1024;
const ACCEPTED = new Set(["image/jpeg", "image/png", "image/webp"]);

export type PickedCover =
  | { status: "cancelled" }
  | { status: "unsupported" }
  | { status: "too_large" }
  | {
      status: "selected";
      uri: string;
      mimeType: string;
      fileName: string | null;
      fileSize: number | null;
    };

export async function pickRecipeCoverImage(): Promise<PickedCover> {
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ["images"],
    allowsMultipleSelection: false,
    exif: false,
  });
  if (result.canceled || result.assets.length === 0) {
    return { status: "cancelled" };
  }
  const asset = result.assets[0];
  const mimeType = asset.mimeType ?? "";
  if (!ACCEPTED.has(mimeType)) {
    return { status: "unsupported" };
  }
  if (asset.fileSize != null && asset.fileSize > MAX_BYTES) {
    return { status: "too_large" };
  }
  return {
    status: "selected",
    uri: asset.uri,
    mimeType,
    fileName: asset.fileName ?? null,
    fileSize: asset.fileSize ?? null,
  };
}

export async function blobFromPickerUri(uri: string, mimeType: string): Promise<Blob> {
  const response = await fetch(uri);
  const blob = await response.blob();
  return blob.type ? blob : new Blob([await blob.arrayBuffer()], { type: mimeType });
}

export function pickerStatusMessage(status: PickedCover["status"]): string | null {
  if (status === "too_large") {
    return "Choose an image smaller than 8 MB.";
  }
  if (status === "unsupported") {
    return "Choose a valid JPEG, PNG, or WebP image.";
  }
  return null;
}

export function coverImageErrorMessage(error: unknown): string | null {
  if (error instanceof ApiUnauthorizedError) {
    return null;
  }
  if (error instanceof ApiNetworkError) {
    return "You’re offline. Check your connection and try again.";
  }
  if (error instanceof ApiError) {
    if (error.status === 413) {
      return "Choose an image smaller than 8 MB.";
    }
    if (error.status === 422) {
      return "Choose a valid JPEG, PNG, or WebP image.";
    }
    if (error.status === 503) {
      return "Images are temporarily unavailable. Your recipe is safe.";
    }
  }
  return "We couldn't upload the image. Please try again.";
}
