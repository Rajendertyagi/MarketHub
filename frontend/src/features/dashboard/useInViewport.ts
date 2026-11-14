import { useEffect, useRef, useState } from "react";

/**
 * Visibility gate for deferring non-critical UI. Returns a ref to attach to the
 * observed element and a boolean that flips to true once it intersects the
 * viewport (and stays true). Falls back to visible when IntersectionObserver is
 * unavailable so content remains reachable in non-browser/test environments.
 */
export function useInViewport<T extends Element>(
  options?: IntersectionObserverInit,
): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null);
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");

  const root = options?.root ?? null;
  const rootMargin = options?.rootMargin;
  const threshold = options?.threshold;

  useEffect(() => {
    if (visible) return;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          obs.disconnect();
        }
      },
      { root, rootMargin, threshold },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [visible, root, rootMargin, threshold]);

  return [ref, visible];
}
