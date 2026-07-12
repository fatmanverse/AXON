/**
 * 配置行 diff 纯函数单测(T2.19)。
 */
import { describe, expect, it } from "vitest";

import { diffLines, diffSummary } from "@/api/configDiff";

describe("diffLines", () => {
  it("相同内容全部标 unchanged", () => {
    const d = diffLines("A=1\nB=2\n", "A=1\nB=2\n");
    expect(d.map((l) => l.kind)).toEqual(["unchanged", "unchanged"]);
  });

  it("新增行标 added", () => {
    const d = diffLines("A=1\n", "A=1\nB=2\n");
    expect(d).toEqual([
      { kind: "unchanged", text: "A=1" },
      { kind: "added", text: "B=2" },
    ]);
  });

  it("删除行标 removed", () => {
    const d = diffLines("A=1\nB=2\n", "A=1\n");
    expect(d).toEqual([
      { kind: "unchanged", text: "A=1" },
      { kind: "removed", text: "B=2" },
    ]);
  });

  it("改一行 = 删旧增新", () => {
    const d = diffLines("A=1\n", "A=2\n");
    const kinds = d.map((l) => l.kind).sort();
    expect(kinds).toEqual(["added", "removed"]);
  });

  it("空串对非空:全 added", () => {
    const d = diffLines("", "X=1\nY=2\n");
    expect(d.every((l) => l.kind === "added")).toBe(true);
    expect(d).toHaveLength(2);
  });

  it("CRLF 归一,不产生虚假差异", () => {
    const d = diffLines("A=1\r\nB=2\r\n", "A=1\nB=2\n");
    expect(d.every((l) => l.kind === "unchanged")).toBe(true);
  });

  it("保留上下文中的公共行(LCS 而非逐行)", () => {
    // old: A C ; new: A B C —— B 为新增,A/C 保持 unchanged
    const d = diffLines("A\nC\n", "A\nB\nC\n");
    expect(d).toEqual([
      { kind: "unchanged", text: "A" },
      { kind: "added", text: "B" },
      { kind: "unchanged", text: "C" },
    ]);
  });
});

describe("diffSummary", () => {
  it("统计增删行数", () => {
    const d = diffLines("A\nB\nC\n", "A\nX\nC\nD\n");
    // B→X 记为 -1(removed B)+1(added X),D 为 +1
    const s = diffSummary(d);
    expect(s).toEqual({ added: 2, removed: 1 });
  });
});
