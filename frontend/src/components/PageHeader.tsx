/**
 * 页面头:统一列表/详情页顶部的标题栏。
 *
 * 替代各页手写的标题 div(字号/字重/间距各写各的),一处定基调:
 *   左侧:页面标题(15px/600)+ 可选内联控件槽(筛选下拉等)
 *   右侧:操作槽(主按钮/Segmented 等)
 * 底部间距统一走 spacing.toolbarGap,页面之间节奏一致。
 */

import { Space } from "antd";

import { colors, spacing } from "@/theme";

export interface PageHeaderProps {
  title: React.ReactNode;
  /** 标题右侧的内联控件槽(筛选/切换等) */
  inline?: React.ReactNode;
  /** 右侧操作区(主按钮等) */
  extra?: React.ReactNode;
}

export function PageHeader({ title, inline, extra }: PageHeaderProps): React.ReactElement {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: 12,
        marginBottom: spacing.toolbarGap,
      }}
    >
      <Space size="middle" wrap>
        <span style={{ fontSize: 15, fontWeight: 600, color: colors.textTitle }}>
          {title}
        </span>
        {inline}
      </Space>
      {extra != null && <Space size="small" wrap>{extra}</Space>}
    </div>
  );
}
