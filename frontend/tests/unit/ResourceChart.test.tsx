/**
 * ResourceChart 部署标注单测(T3.5,设计 §9.2)。
 *
 * 验证传入 markers 时,ECharts option 的第一条 series 上挂出 markLine(部署时间点
 * 竖线),且竖线数据与 markers 一一对应;不传 markers 时不挂 markLine。
 * ECharts 组件桩成暴露 option 的占位,只断言 option 结构,不触真实 canvas 渲染。
 */

import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

import type { LineSeries } from "@/api/metricsTransform";

// 捕获传给 ECharts 的 option,供断言。仅需断言 series[0].markLine 结构。
interface CapturedOption {
  series: Array<{ markLine?: { data: Array<Record<string, unknown>> } }>;
}
let capturedOption: CapturedOption | null = null;
vi.mock("echarts-for-react", () => ({
  default: ({ option }: { option: unknown }) => {
    capturedOption = option as CapturedOption;
    return <div data-testid="echart" />;
  },
}));

import { ResourceChart, type DeployMarker } from "@/components/ResourceChart";

const SERIES: LineSeries[] = [
  { name: "CPU", points: [{ t: 1720000000000, v: 12 }, { t: 1720000060000, v: 14 }] },
];

describe("ResourceChart 部署标注", () => {
  it("传入 markers 时第一条 series 挂 markLine 竖线", () => {
    const markers: DeployMarker[] = [
      { t: 1720000030000, label: "v1.2.0" },
      { t: 1720000050000, label: "v1.2.1" },
    ];
    render(<ResourceChart title="CPU" series={SERIES} markers={markers} />);

    const first = capturedOption!.series[0];
    expect(first.markLine).toBeTruthy();
    expect(first.markLine!.data).toHaveLength(2);
    expect(first.markLine!.data[0]).toMatchObject({ xAxis: 1720000030000, name: "v1.2.0" });
    expect(first.markLine!.data[1]).toMatchObject({ xAxis: 1720000050000, name: "v1.2.1" });
  });

  it("不传 markers 时不挂 markLine", () => {
    capturedOption = null;
    render(<ResourceChart title="CPU" series={SERIES} />);
    expect(capturedOption!.series[0].markLine).toBeUndefined();
  });
});
