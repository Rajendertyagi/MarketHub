// Single shared HTTP client for the MarketHub frontend.
//
// Components must NOT scatter raw `fetch()` calls. All server access goes
// through `request()`, which centralizes:
//   - request construction (base path + query params)
//   - abort/cancellation (AbortSignal)
//   - non-2xx handling
//   - JSON parsing + backend error payload extraction
//   - Zod contract validation
//   - a predictable typed error (ApiError) surfaced to React
//
// It does NOT redesign backend semantics; it only consumes existing contracts.

import { ApiError } from "@/types";
import { z } from "zod";

const API_BASE = "/api";

export interface RequestOptions {
  /** Query parameters. `undefined`/`null` values are omitted. */
  params?: Record<string, string | number | boolean | undefined | null>;
  /** Abort signal for cancellation (TanStack Query supplies this). */
  signal?: AbortSignal;
  /** Optional Zod schema; when provided, the parsed response is validated. */
  schema?: z.ZodType<unknown>;
  /** HTTP method (default GET). */
  method?: "GET" | "POST" | "PUT" | "DELETE";
  /** JSON request body (for POST/PUT). Sent as application/json. */
  body?: unknown;
}

function buildIdlessUrl(
  path: string,
  params?: RequestOptions["params"],
): string {
  // Every endpoint lives under the API base. Call sites pass paths like
  // "/market/breadth"; ensure the "/api" prefix is always present (paths that
  // already include it are left untouched).
  const fullPath = path.startsWith("/api/")
    ? path
    : `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
  const url = new URL(fullPath, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  // Keep the configured API base when the page is served from /ui.
  return url.pathname + url.search;
}

async function readErrorPayload(resp: Response): Promise<string> {
  try {
    const data = await resp.json();
    if (data && typeof data === "object") {
      const msg = (data as Record<string, unknown>).error ??
        (data as Record<string, unknown>).message;
      if (typeof msg === "string" && msg.length) return msg;
    }
  } catch {
    // Non-JSON error body — fall through to status text.
  }
  return `Request failed (HTTP ${resp.status})`;
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const url = buildIdlessUrl(path, options.params);
  const headers: Record<string, string> = { Accept: "application/json" };
  const init: RequestInit = {
    method: options.method ?? "GET",
    headers,
    signal: options.signal,
  };
  if (
    options.body !== undefined &&
    options.method !== undefined &&
    options.method !== "GET"
  ) {
    init.body = JSON.stringify(options.body);
    headers["Content-Type"] = "application/json";
  }
  let resp: Response;
  try {
    resp = await fetch(url, init);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError("abort", "Request aborted", { status: 0 });
    }
    if (
      typeof err === "object" &&
      err !== null &&
      "name" in err &&
      (err as { name?: string }).name === "AbortError"
    ) {
      throw new ApiError("abort", "Request aborted", { status: 0 });
    }
    throw new ApiError(
      "network",
      "Network error — MarketHub backend may be offline.",
      { status: 0 },
    );
  }

  if (!resp.ok) {
    const message = await readErrorPayload(resp);
    throw new ApiError("http", message, { status: resp.status });
  }

  let json: unknown;
  try {
    json = await resp.json();
  } catch {
    throw new ApiError("parse", "Invalid JSON response from server.", {
      status: resp.status,
    });
  }

  if (options.schema) {
    const result = options.schema.safeParse(json);
    if (!result.success) {
      throw new ApiError("parse", "Response did not match the expected contract.", {
        status: resp.status,
        payload: result.error.issues,
      });
    }
    return result.data as T;
  }

  return json as T;
}
