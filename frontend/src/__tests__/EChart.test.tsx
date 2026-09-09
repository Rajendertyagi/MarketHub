import { afterEach, describe, expect, it, vi } from "vitest";
import { render, cleanup } from "@testing-library/react";
import type { EChartsOption } from "echarts";

const { echartsMock } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  const init = vi.fn(() => ({ setOption, resize, dispose }));
  return { echartsMock: { init, setOption, resize, dispose } };
});

vi.mock("echarts", () => ({ init: echartsMock.init }));

import { EChart } from "@/components/EChart";

afterEach(() => cleanup());

const option = {
  xAxis: { type: "category", data: ["a", "b"] },
  series: [{ type: "line", data: [1, 2] }],
} as unknown as EChartsOption;

describe("EChart lifecycle", () => {
  it("initializes, applies option, and disposes on unmount", () => {
    const { unmount } = render(<EChart option={option} />);
    expect(echartsMock.init).toHaveBeenCalledTimes(1);
    expect(echartsMock.setOption).toHaveBeenCalled();

    unmount();
    expect(echartsMock.dispose).toHaveBeenCalledTimes(1);
  });

  it("re-applies option when option prop changes", () => {
    const { rerender } = render(<EChart option={option} />);
    const before = echartsMock.setOption.mock.calls.length;
    rerender(
      <EChart
        option={
          {
            xAxis: { type: "category", data: ["c"] },
            series: [{ type: "line", data: [3] }],
          } as unknown as EChartsOption
        }
      />,
    );
    expect(echartsMock.setOption.mock.calls.length).toBeGreaterThan(before);
  });
});
