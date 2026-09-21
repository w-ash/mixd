import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { server } from "#/test/setup";

import { ApiError, customFetch } from "./client";

async function catchApiError(url: string): Promise<ApiError> {
  try {
    await customFetch(url);
  } catch (e) {
    if (e instanceof ApiError) return e;
    throw e;
  }
  throw new Error("Expected customFetch to throw");
}

describe("customFetch", () => {
  it("returns envelope with undefined data for 204 No Content", async () => {
    server.use(
      http.get("*/test-204", () => {
        return new HttpResponse(null, { status: 204 });
      }),
    );

    const result = await customFetch<{
      data: undefined;
      status: number;
    }>("/test-204");

    expect(result.data).toBeUndefined();
    expect(result.status).toBe(204);
  });

  it("wraps response body in envelope for 200 success", async () => {
    server.use(
      http.get("*/test-200", () => {
        return HttpResponse.json({ name: "test" }, { status: 200 });
      }),
    );

    const result = await customFetch<{
      data: { name: string };
      status: number;
      headers: Headers;
    }>("/test-200");

    expect(result.data).toEqual({ name: "test" });
    expect(result.status).toBe(200);
    expect(result.headers).toBeInstanceOf(Headers);
  });

  it("throws ApiError with parsed envelope on error response", async () => {
    server.use(
      http.get("*/test-error", () => {
        return HttpResponse.json(
          {
            error: {
              code: "NOT_FOUND",
              message: "Playlist not found",
              details: { id: "123" },
            },
          },
          { status: 404 },
        );
      }),
    );

    const error = await catchApiError("/test-error");

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.code).toBe("NOT_FOUND");
    expect(error.message).toBe("Playlist not found");
    expect(error.details).toEqual({ id: "123" });
  });

  it("throws ApiError with UNKNOWN_ERROR when error has no envelope", async () => {
    server.use(
      http.get("*/test-raw-error", () => {
        return HttpResponse.json({ something: "unexpected" }, { status: 500 });
      }),
    );

    const error = await catchApiError("/test-raw-error");

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(500);
    expect(error.code).toBe("UNKNOWN_ERROR");
    expect(error.message).toBe("An unknown error occurred");
    expect(error.details).toBeUndefined();
  });
});
