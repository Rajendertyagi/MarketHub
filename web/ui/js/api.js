/**
 * MarketHub WebUI — shared REST helpers.
 *
 * Thin JSON wrappers over fetch with consistent error reporting.
 * Feature modules (news/sources/logs) build their endpoints on these.
 */

async function _readError(resp, fallback) {
  try {
    const data = await resp.json();
    if (data && data.message) return data.message;
  } catch { /* not JSON — fall through */ }
  return `${fallback} (HTTP ${resp.status})`;
}

export async function apiGet(path) {
  const resp = await fetch(path);
  if (!resp.ok) throw new Error(await _readError(resp, "Request failed"));
  return resp.json();
}

async function _send(method, path, body) {
  const resp = await fetch(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await resp.json(); } catch { /* empty/non-JSON body */ }
  if (!resp.ok) {
    throw new Error((data && data.message) || `Request failed (HTTP ${resp.status})`);
  }
  return data;
}

export const apiPost = (path, body) => _send("POST", path, body);
export const apiPut = (path, body) => _send("PUT", path, body);
export const apiDelete = (path) => _send("DELETE", path, undefined);
