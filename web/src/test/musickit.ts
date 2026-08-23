import { vi } from "vitest";

/**
 * Install a `window.MusicKit` mock (the loader's sync guard picks it up,
 * so the CDN script is never touched in tests).
 *
 * Callers own cleanup: reset `window.MusicKit = undefined` in `afterEach`.
 */
export function mockMusicKit(
  authorize = vi.fn().mockResolvedValue("mut-123"),
): {
  configure: ReturnType<typeof vi.fn>;
  authorize: typeof authorize;
} {
  const configure = vi.fn().mockResolvedValue(undefined);
  window.MusicKit = {
    configure,
    getInstance: () => ({ authorize }),
  };
  return { configure, authorize };
}
