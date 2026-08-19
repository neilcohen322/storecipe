import { useEffect, useRef, useState, type ReactNode } from "react";
import { Image } from "react-native";

import type { CoverImageResponse } from "../api/catalog";

export type CoverImageLoader = (args: {
  recipeId: string;
  url: string;
  etag?: string;
  signal: AbortSignal;
}) => Promise<CoverImageResponse>;

export type AuthenticatedRecipeImageProps = {
  recipeId: string;
  title: string;
  etag: string;
  url: string;
  loadCoverImage: CoverImageLoader;
  fallback: ReactNode;
};

export function AuthenticatedRecipeImage({
  recipeId,
  title,
  etag,
  url,
  loadCoverImage,
  fallback,
}: AuthenticatedRecipeImageProps) {
  const [objectUrl, setObjectUrl] = useState<string | null>(null);
  const requestId = useRef(0);
  const objectUrlRef = useRef<string | null>(null);
  const cacheRef = useRef<{ recipeId: string; etag: string; objectUrl: string } | null>(null);

  useEffect(() => {
    const currentRequest = ++requestId.current;
    const controller = new AbortController();
    let createdUrl: string | null = null;
    const cache = cacheRef.current;
    const cachedBytesMatch =
      cache != null && cache.recipeId === recipeId && cache.etag === etag && cache.objectUrl.length > 0;

    void loadCoverImage({
      recipeId,
      url,
      etag: cachedBytesMatch ? etag : undefined,
      signal: controller.signal,
    }).then(
      (result) => {
        if (currentRequest !== requestId.current) {
          return;
        }
        if (result.notModified) {
          if (cachedBytesMatch && cache != null) {
            objectUrlRef.current = cache.objectUrl;
            setObjectUrl(cache.objectUrl);
            return;
          }
          setObjectUrl(null);
          return;
        }
        if (!result.blob) {
          cacheRef.current = null;
          setObjectUrl(null);
          return;
        }
        createdUrl = URL.createObjectURL(result.blob);
        if (objectUrlRef.current && objectUrlRef.current !== createdUrl) {
          URL.revokeObjectURL(objectUrlRef.current);
        }
        objectUrlRef.current = createdUrl;
        cacheRef.current = { recipeId, etag: result.etag ?? etag, objectUrl: createdUrl };
        setObjectUrl(createdUrl);
      },
      () => {
        if (currentRequest !== requestId.current) {
          return;
        }
        setObjectUrl(null);
      },
    );

    return () => {
      controller.abort();
    };
  }, [recipeId, etag, url, loadCoverImage]);

  useEffect(() => {
    if (!objectUrl) {
      return;
    }
    return () => {
      URL.revokeObjectURL(objectUrl);
      if (objectUrlRef.current === objectUrl) {
        objectUrlRef.current = null;
      }
      if (cacheRef.current?.objectUrl === objectUrl) {
        cacheRef.current = null;
      }
    };
  }, [objectUrl]);

  if (!objectUrl) {
    return fallback;
  }

  return (
    <Image
      testID="recipe-cover-image"
      accessibilityRole="image"
      accessibilityLabel={`Cover image for ${title}`}
      source={{ uri: objectUrl }}
      style={{ width: "100%", height: "100%" }}
      resizeMode="cover"
    />
  );
}
