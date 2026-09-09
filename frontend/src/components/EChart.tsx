import { useEffect, useRef } from "react";
import * as echarts from "echarts";
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

  latestOption.current = option;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const instance = echarts.init(el, undefined, { renderer: "canvas" });
    instanceRef.current = instance;
    instance.setOption(latestOption.current);
    onInit?.(instance);

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
    // Init/dispose only on mount/unmount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Apply option updates on every render where `option` identity changes.
  useEffect(() => {
    instanceRef.current?.setOption(option);
  }, [option]);

  return (
    <div
      ref={containerRef}
      className={className ?? "chart-container"}
      style={style}
    />
  );
}
