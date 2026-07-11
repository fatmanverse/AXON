/**
 * Prometheus matrix → ECharts 折线序列转换的单测(T1.18)。
 * 纯逻辑:序列命名回退、字符串转数值、NaN 过滤、非 matrix 空态。
 */

import { describe, expect, it } from "vitest";

import type { PromData } from "@/api/metrics";
import { matrixToSeries, seriesName } from "@/api/metricsTransform";

describe("seriesName", () => {
  it("优先取 instance", () => {
    expect(seriesName({ instance: "10.0.0.1:9100", job: "node" })).toBe("10.0.0.1:9100");
  });

  it("无 instance 时退到 __name__", () => {
    expect(seriesName({ __name__: "node_load1" })).toBe("node_load1");
  });

  it("都没有时拼接标签", () => {
    expect(seriesName({ mode: "idle", cpu: "0" })).toBe("mode=idle,cpu=0");
  });

  it("空标签给兜底名", () => {
    expect(seriesName({})).toBe("series");
  });
});

describe("matrixToSeries", () => {
  it("把 matrix 转成折线序列,ts 转毫秒、值转数值", () => {
    const data: PromData = {
      resultType: "matrix",
      result: [
        {
          metric: { instance: "10.0.0.1:9100" },
          values: [
            [100, "0.5"],
            [115, "0.8"],
          ],
        },
      ],
    };

    const series = matrixToSeries(data);

    expect(series).toHaveLength(1);
    expect(series[0].name).toBe("10.0.0.1:9100");
    expect(series[0].points).toEqual([
      { t: 100000, v: 0.5 },
      { t: 115000, v: 0.8 },
    ]);
  });

  it("过滤非有限数值(如 NaN)", () => {
    const data: PromData = {
      resultType: "matrix",
      result: [
        {
          metric: { instance: "h1" },
          values: [
            [100, "NaN"],
            [115, "1.2"],
          ],
        },
      ],
    };

    const series = matrixToSeries(data);
    expect(series[0].points).toEqual([{ t: 115000, v: 1.2 }]);
  });

  it("非 matrix 结果返回空数组", () => {
    const data: PromData = { resultType: "vector", result: [] };
    expect(matrixToSeries(data)).toEqual([]);
  });
});
