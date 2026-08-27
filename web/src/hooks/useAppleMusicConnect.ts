import { useCallback, useEffect, useRef, useState } from "react";

import {
  getMusickitConfigApiV1ConnectorsAppleMusicMusickitConfigGet,
  useStoreAppleMusicTokenApiV1ConnectorsAppleMusicTokenPost,
} from "#/api/generated/auth/auth";
import { toasts } from "#/lib/toasts";

/** The subset of a configured MusicKit instance the connect flow touches. */
export interface MusicKitInstance {
  /** Opens Apple's login sheet; resolves to the Music User Token. */
  authorize(): Promise<string | undefined>;
}

/** The subset of the MusicKit JS v3 global the connect flow touches. */
export interface MusicKitStatic {
  configure(config: {
    developerToken: string;
    app: { name: string; build: string };
  }): Promise<unknown>;
  getInstance(): MusicKitInstance;
}

declare global {
  interface Window {
    MusicKit?: MusicKitStatic;
  }
}

const MUSICKIT_JS_URL =
  "https://js-cdn.music.apple.com/musickit/v3/musickit.js";

/** How long a CDN load may hang before the attempt fails (and can retry). */
const MUSICKIT_LOAD_TIMEOUT_MS = 15_000;

let musicKitLoading: Promise<MusicKitStatic> | null = null;

/**
 * Load MusicKit JS from Apple's CDN once, resolving to the global.
 *
 * Module-level promise cache: concurrent or repeated connects share one
 * script tag. `musickitloaded` can fire before a listener registers (a
 * cached script evaluating first) — so check `window.MusicKit`
 * synchronously, subscribe otherwise. Every failure path — script error,
 * loaded-without-global, load timeout — clears the cache so a later
 * Connect click retries the load instead of awaiting a dead promise
 * forever. Exported as the injection seam for tests (and the sync guard
 * means a test-installed `window.MusicKit` mock short-circuits the CDN
 * entirely).
 */
export function loadMusicKit(): Promise<MusicKitStatic> {
  if (window.MusicKit) return Promise.resolve(window.MusicKit);
  musicKitLoading ??= new Promise<MusicKitStatic>((resolve, reject) => {
    const fail = (error: Error) => {
      // Clear the cache so a later Connect click can retry the load.
      musicKitLoading = null;
      clearTimeout(timer);
      reject(error);
    };
    const timer = setTimeout(
      () => fail(new Error("Timed out loading MusicKit JS")),
      MUSICKIT_LOAD_TIMEOUT_MS,
    );
    document.addEventListener(
      "musickitloaded",
      () => {
        if (window.MusicKit) {
          clearTimeout(timer);
          resolve(window.MusicKit);
        } else {
          fail(new Error("MusicKit JS loaded without window.MusicKit"));
        }
      },
      { once: true },
    );
    const script = document.createElement("script");
    script.src = MUSICKIT_JS_URL;
    script.async = true;
    script.onerror = () => fail(new Error("Failed to load MusicKit JS"));
    document.head.appendChild(script);
  });
  return musicKitLoading;
}

function lowerMessage(err: unknown): string {
  return (err instanceof Error ? err.message : String(err)).toLowerCase();
}

/**
 * A user closing Apple's login sheet is a deliberate dismissal, not an
 * error. MusicKit v3 documents no single dismissal error: MKError carries
 * `errorCode: "ACCESS_DENIED"` when the user refuses the request, and
 * other builds reject with browser-dependent cancel-shaped messages — so
 * match the error code first, substring `cancel` as the fallback.
 * Deliberately narrow: matching `popup`/`closed` too would silently
 * swallow blocked popups as "canceled".
 */
function isUserDismissal(err: unknown): boolean {
  if (
    typeof err === "object" &&
    err !== null &&
    "errorCode" in err &&
    err.errorCode === "ACCESS_DENIED"
  ) {
    return true;
  }
  return lowerMessage(err).includes("cancel");
}

/** A rejection mentioning the popup means the browser blocked Apple's sheet. */
function isPopupBlocked(err: unknown): boolean {
  return lowerMessage(err).includes("popup");
}

interface UseAppleMusicConnectOptions {
  /**
   * Eagerly run the connect prerequisites on mount (config fetch →
   * MusicKit JS load → `configure()`), shrinking the click chain to
   * `authorize()`. Fire-and-forget: a prewarm failure never toasts at
   * mount — it is held (and retried) for the Connect click to surface.
   */
  prewarm?: boolean;
  /** Injection seam for tests. */
  loadMusicKitImpl?: () => Promise<MusicKitStatic>;
}

/**
 * Connect Apple Music in-app via MusicKit JS.
 *
 * Runs the whole flow from the app page — the only popup the user ever
 * sees is Apple's own login sheet. Setup (developer token from
 * `/connectors/apple_music/musickit-config`, MusicKit JS load,
 * `configure()`) runs eagerly on card mount when `prewarm` is set, which
 * is Apple's documented configure-at-load integration shape. On connect:
 * await the (usually settled) setup, `authorize()`, POST the resulting
 * Music User Token, then await the connectors refetch settle (same
 * ordering contract as `useDiscogsToken`) before the success toast — the
 * toast never races ahead of the card flip.
 *
 * Gesture context: `authorize()` opens Apple's sheet, which rides the
 * Connect click's transient user activation. With the setup prewarmed the
 * click chain is effectively just `authorize()`, keeping even Safari's
 * strict activation budget happy. If the prewarm failed, the click retries
 * the full chain — Chrome/Firefox tolerate the extra awaits.
 */
export function useAppleMusicConnect({
  prewarm = false,
  loadMusicKitImpl = loadMusicKit,
}: UseAppleMusicConnectOptions = {}) {
  const [isConnecting, setIsConnecting] = useState(false);
  const storeToken = useStoreAppleMusicTokenApiV1ConnectorsAppleMusicTokenPost({
    // The single catch below owns error surfacing — no global toast on top.
    // `awaitInvalidation` holds the mutation open until the connectors refetch
    // lands, so the success toast never precedes the card it describes.
    mutation: { meta: { suppressErrorToast: true, awaitInvalidation: true } },
  });
  const setupRef = useRef<Promise<MusicKitInstance> | null>(null);

  // One setup attempt (config fetch + load + configure) shared per mounted
  // card between prewarm and click; a failed attempt clears itself so the
  // next Connect click retries from scratch instead of replaying a stale
  // rejection.
  const ensureSetup = useCallback((): Promise<MusicKitInstance> => {
    setupRef.current ??= (async () => {
      const config =
        await getMusickitConfigApiV1ConnectorsAppleMusicMusickitConfigGet();
      const musicKit = await loadMusicKitImpl();
      await musicKit.configure({
        developerToken: config.data.developer_token,
        app: { name: "Mixd", build: __APP_VERSION__ || "web" },
      });
      return musicKit.getInstance();
    })().catch((err: unknown) => {
      setupRef.current = null;
      throw err;
    });
    return setupRef.current;
  }, [loadMusicKitImpl]);

  useEffect(() => {
    if (!prewarm) return;
    // Fire-and-forget; errors are held for the click — a mount never toasts.
    ensureSetup().catch(() => undefined);
  }, [prewarm, ensureSetup]);

  async function connect() {
    if (isConnecting) return;
    setIsConnecting(true);
    try {
      const instance = await ensureSetup();

      let musicUserToken: string | undefined;
      try {
        musicUserToken = await instance.authorize();
      } catch (err) {
        if (isUserDismissal(err)) {
          toasts.message("Apple Music connection canceled");
          return;
        }
        if (isPopupBlocked(err)) {
          toasts.error("Allow popups for this site, then try again", err);
          return;
        }
        throw err;
      }
      if (!musicUserToken) {
        // Some MusicKit builds resolve without a token on dismissal.
        toasts.message("Apple Music connection canceled");
        return;
      }

      await storeToken.mutateAsync({
        data: { music_user_token: musicUserToken },
      });
      toasts.success("Apple Music connected");
    } catch (err) {
      toasts.error("Failed to connect Apple Music", err);
    } finally {
      setIsConnecting(false);
    }
  }

  return { connect, isConnecting };
}
