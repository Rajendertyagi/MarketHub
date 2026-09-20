import { afterEach, describe, expect, it, vi } from "vitest";
import { request } from "@/api/client";
import { historyResponseSchema } from "@/api/schemas";
import { ApiError } from "@/types";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function fakeResponse(opts: { ok: boolean; status: number; json: unknown }): Response {
  return {
    ok: opts.ok,
    status: opts.status,
    json: async () => opts.json,
  } as unknown as Response;
}

describe("api client error taxonomy", () => {
  it("classifies network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network down")));
    await expect(request("/x")).rejects.toMatchObject({
      kind: "network",
    });
  });

  it("classifies non-2xx with backend error payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(fakeResponse({ ok: false, status: 400, json: { error: "bad input" } })),
    );
    const err = await request("/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).kind).toBe("http");
    expect((err as ApiError).status).toBe(400);
    expect((err as ApiError).message).toBe("bad input");
  });

  it("classifies unsupported-provider message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        fakeResponse({
          ok: false,
          status: 400,
          json: { error: "History is unsupported for this provider" },
        }),
      ),
    );
    const err = (await request("/x").catch((e) => e)) as ApiError;
    expect(err.isUnsupported).toBe(true);
  });

  it("classifies Zod contract validation failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fakeResponse({ ok: true, status: 200, json: { candles: "nope" } })),
    );
    const err = (await request("/x", {
      schema: historyResponseSchema,
    }).catch((e) => e)) as ApiError;
    expect(err.kind).toBe("parse");
  });

  it("classifies abort", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue({ name: "AbortError" }));
    const err = (await request("/x").catch((e) => e)) as ApiError;
    expect(err.kind).toBe("abort");
  });

  it("returns parsed data on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(fakeResponse({ ok: true, status: 200, json: { candles: [] } })),
    );
    const data = await request("/x", { schema: historyResponseSchema });
    expect(data).toEqual({ candles: [] });
  });
});
