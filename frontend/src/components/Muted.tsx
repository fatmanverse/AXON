/**
 * 弱提示文本。用于表格空值占位("—")或"未设置"等次要说明,统一取 theme 的
 * 占位色(colors.textPlaceholder),消除此前散落各页的硬编码 #B0B3B5。
 *
 * 默认渲染一个破折号("—"),也可传入 children 覆盖(如"未设置")。
 */

import { colors } from "@/theme";

interface MutedProps {
  children?: React.ReactNode;
}

export function Muted({ children }: MutedProps): React.ReactElement {
  return <span style={{ color: colors.textPlaceholder }}>{children ?? "—"}</span>;
}
