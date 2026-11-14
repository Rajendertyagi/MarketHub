import * as echarts from "echarts/core";
import { BarChart, TreemapChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import { THEME_CHANGE_EVENT } from "@/app/ThemeProvider";

echarts.use([BarChart, TreemapChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);

type EChartsOption = echarts.EChartsCoreOption;
type EChartsInstance = ReturnType<typeof echarts.init>;

interface EChartProps {
  option: EChartsOption;
  /** Called with the instance once initialized (e.g. for tests). */
  onInit?: (instance: EChartsInstance) => void;
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
  const instanceRef = useRef<EChartsInstance | null>(null);
  const latestOption = useRef<EChartsOption>(option);
  // Latest-callback ref: the mount effect must run exactly once
  // (init/dispose lifecycle), so it reads onInit through a ref instead
  // of subscribing to its identity.
  const onInitRef = useRef(onInit);

  latestOption.current = option;
  onInitRef.current = onInit;

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    let instance: EChartsInstance | null = null;
    try {
      instance = echarts.init(el, undefined, { renderer: "canvas" });
      instanceRef.current = instance;
      instance.setOption(latestOption.current);
      onInitRef.current?.(instance);
    } catch {
      // Canvas/rendering may be unavailable in non-browser test environments.
      instanceRef.current = null;
      return;
    }

    const observer = new ResizeObserver(() => {
      try {
        instance?.resize();
      } catch {
        // ignore resize errors when renderer is unavailable
      }
    });
    observer.observe(el);

    const onThemeChange = () => {
      try {
        instance?.setOption(latestOption.current);
      } catch {
        // ignore
      }
    };
    window.addEventListener(THEME_CHANGE_EVENT, onThemeChange);

    return () => {
      observer.disconnect();
      window.removeEventListener(THEME_CHANGE_EVENT, onThemeChange);
      try {
        instance?.dispose();
      } catch {
        // ignore
      }
      instanceRef.current = null;
    };
    // Init/dispose only on mount/unmount; option/theme updates flow
    // through refs and the dedicated update effect below.
  }, []);

  // Apply option updates on every render where `option` identity changes.
  useEffect(() => {
    try {
      instanceRef.current?.setOption(option);
    } catch {
      // ignore
    }
  }, [option]);

  return <div ref={containerRef} className={className ?? "chart-container"} style={style} />;
}
