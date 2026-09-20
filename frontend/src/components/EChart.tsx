import * as echarts from "echarts";
import { useEffect, useRef } from "react";
import { THEME_CHANGE_EVENT } from "@/app/ThemeProvider";

interface EChartProps {
  option: echarts.EChartsOption;
  /** Called with the instance once initialized (e.g. for tests). */
  onInit?: (instance: echarts.ECharts) => void;
  className?: string;
  style?: React.CSSProperties;
}

// Single, reusable ECharts lifecycle owner.
//
// Lifecycle:
//   mount        -> echarts.init
//   option change -> setOption
//   resize       -> ResizeObserver -> resize
//   theme change -> re-apply latest option (recolored via cssVar at build time)
//   unmount      -> dispose
//
// Guarantees: exactly one instance, no leaked ResizeObservers/listeners, no
// import-time DOM access.
export function EChart({ option, onInit, className, style }: EChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const instanceRef = useRef<echarts.ECharts | null>(null);
  const latestOption = useRef<echarts.EChartsOption>(option);
  // Latest-callback ref: the mount effect must run exactly once
  // (init/dispose lifecycle), so it reads onInit through a ref instead
  // of subscribing to its identity.
  const onInitRef = useRef(onInit);

  latestOption.current = option;
  onInitRef.current = onInit;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const instance = echarts.init(el, undefined, { renderer: "canvas" });
    instanceRef.current = instance;
    instance.setOption(latestOption.current);
    onInitRef.current?.(instance);

    const observer = new ResizeObserver(() => instance.resize());
    observer.observe(el);

    const onThemeChange = () => instance.setOption(latestOption.current);
    window.addEventListener(THEME_CHANGE_EVENT, onThemeChange);

    return () => {
      observer.disconnect();
      window.removeEventListener(THEME_CHANGE_EVENT, onThemeChange);
      instance.dispose();
      instanceRef.current = null;
    };
    // Init/dispose only on mount/unmount; option/theme updates flow
    // through refs and the dedicated update effect below.
  }, []);

  // Apply option updates on every render where `option` identity changes.
  useEffect(() => {
    instanceRef.current?.setOption(option);
  }, [option]);

  return <div ref={containerRef} className={className ?? "chart-container"} style={style} />;
}
