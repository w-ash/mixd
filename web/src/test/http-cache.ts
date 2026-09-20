/**
 * Browser HTTP cache stand-in for jsdom.
 *
 * jsdom has none, so MSW answers every request from the handler and a test
 * cannot see what a real browser does with `Cache-Control: max-age`: serve the
 * previous body without asking the server. That gap hid a bug where the refetch
 * after an optimistic write was answered from the browser cache with the
 * pre-write body, and the flipped control snapped back.
 *
 * Honours `max-age` on GETs and respects the request's cache mode, so a fetch
 * that asks to revalidate (`no-cache`) or to skip the cache (`no-store`,
 * `reload`) reaches the network — which is exactly what the fix relies on.
 */

/** Cache modes that must not be answered from a stored response. */
const BYPASS: ReadonlySet<string> = new Set(["no-cache", "no-store", "reload"]);

function maxAgeMs(response: Response): number {
  const directive = /max-age=(\d+)/.exec(
    response.headers.get("cache-control") ?? "",
  );
  return directive ? Number(directive[1]) * 1000 : 0;
}

/**
 * Install the cache in front of `globalThis.fetch`.
 *
 * Returns the uninstall function — call it in `afterEach`, or the next test
 * inherits both the patch and the stored responses.
 */
export function installHttpCache(): () => void {
  const stored = new Map<string, { response: Response; expiresAt: number }>();
  const original = globalThis.fetch;

  globalThis.fetch = async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    const url = typeof input === "string" ? input : String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const cacheable = method === "GET" && !BYPASS.has(init?.cache ?? "default");

    if (cacheable) {
      const hit = stored.get(url);
      if (hit && hit.expiresAt > Date.now()) return hit.response.clone();
    }

    const response = await original(input, init);

    if (method === "GET") {
      const ttl = maxAgeMs(response);
      if (ttl > 0) {
        stored.set(url, {
          response: response.clone(),
          expiresAt: Date.now() + ttl,
        });
      }
    }
    return response;
  };

  return () => {
    globalThis.fetch = original;
    stored.clear();
  };
}
