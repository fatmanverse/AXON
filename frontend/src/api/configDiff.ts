/**
 * 配置版本行级 diff(T2.19,§12.1)。
 *
 * 纯函数:对两个版本的文本内容做基于最长公共子序列(LCS)的行 diff,产出
 * 逐行的 unchanged/added/removed 标注,供 UI 渲染"与上一版对比"。放在 api 层
 * 是为可独立单测(不依赖 React),与 metricsTransform 同思路。
 */

export type DiffKind = "unchanged" | "added" | "removed";

export interface DiffLine {
  kind: DiffKind;
  /** 该行文本(不含换行)。 */
  text: string;
}

/**
 * 计算 old→new 的行级 diff。基于 LCS:公共行标 unchanged,仅在 new 出现标 added,
 * 仅在 old 出现标 removed。空串按 0 行处理。
 */
export function diffLines(oldText: string, newText: string): DiffLine[] {
  const oldLines = splitLines(oldText);
  const newLines = splitLines(newText);
  const lcs = lcsTable(oldLines, newLines);

  const result: DiffLine[] = [];
  let i = oldLines.length;
  let j = newLines.length;
  // 从右下角回溯 LCS 表,构造 diff(逆序生成,最后反转)
  while (i > 0 && j > 0) {
    if (oldLines[i - 1] === newLines[j - 1]) {
      result.push({ kind: "unchanged", text: oldLines[i - 1] });
      i -= 1;
      j -= 1;
    } else if (lcs[i - 1][j] >= lcs[i][j - 1]) {
      result.push({ kind: "removed", text: oldLines[i - 1] });
      i -= 1;
    } else {
      result.push({ kind: "added", text: newLines[j - 1] });
      j -= 1;
    }
  }
  while (i > 0) {
    result.push({ kind: "removed", text: oldLines[i - 1] });
    i -= 1;
  }
  while (j > 0) {
    result.push({ kind: "added", text: newLines[j - 1] });
    j -= 1;
  }
  return result.reverse();
}

/** 统计 diff 中新增/删除行数,供摘要展示(如"+3 -1")。 */
export function diffSummary(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const line of lines) {
    if (line.kind === "added") added += 1;
    else if (line.kind === "removed") removed += 1;
  }
  return { added, removed };
}

function splitLines(text: string): string[] {
  if (text === "") return [];
  // 统一换行,去掉末尾空行造成的多余空项
  const normalized = text.replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");
  if (lines.length > 0 && lines[lines.length - 1] === "") {
    lines.pop();
  }
  return lines;
}

function lcsTable(a: string[], b: string[]): number[][] {
  const rows = a.length + 1;
  const cols = b.length + 1;
  const table: number[][] = Array.from({ length: rows }, () =>
    new Array<number>(cols).fill(0),
  );
  for (let i = 1; i < rows; i += 1) {
    for (let j = 1; j < cols; j += 1) {
      if (a[i - 1] === b[j - 1]) {
        table[i][j] = table[i - 1][j - 1] + 1;
      } else {
        table[i][j] = Math.max(table[i - 1][j], table[i][j - 1]);
      }
    }
  }
  return table;
}
