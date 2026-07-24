/**
 * 列表页表格工具栏(对齐 JumpServer 列表页布局)。
 *
 * 一条横栏统一承载列表页的通用能力,让各列表页从「标题 + 一个按钮 + 裸表格」
 * 升级为运维台常见的密集工具栏:
 *   左侧:标题 + 业务筛选槽(env/runtime 等,由各页传入)
 *   右侧:即时搜索框 + 刷新 + 列显隐设置 + 主操作(新建/纳管等)
 *
 * 设计取舍:
 * - 搜索为**前端即时过滤**(当前列表无后端分页、数据量小),由父页拿 searchValue
 *   自行过滤 dataSource,本组件只负责受控输入,不知业务字段。
 * - 列设置为可选能力:父页给出可切换的列定义与当前可见集合,本组件用下拉多选
 *   回吐新集合,父页据此裁剪 columns。不传则不渲染该入口。
 * - 所有能力均可选,按需组合;不塞业务逻辑,保持纯展示 + 回调。
 */

import { ReloadOutlined, SearchOutlined, SettingOutlined } from "@ant-design/icons";
import { Button, Checkbox, Dropdown, Input, Space, Tooltip } from "antd";

import { colors, spacing } from "@/theme";

export interface ColumnToggle {
  /** 列 key,与 Table columns 的 key 对应 */
  key: string;
  /** 列显示名 */
  label: string;
}

export interface ColumnSettings {
  /** 可切换显隐的列(通常排除「操作」这类固定列) */
  options: ColumnToggle[];
  /** 当前可见列的 key 集合 */
  value: string[];
  /** 勾选变化时回吐新的可见集合 */
  onChange: (visibleKeys: string[]) => void;
}

export interface TableToolbarProps {
  /** 左侧标题(通常是页面名) */
  title?: React.ReactNode;
  /** 标题右侧的业务筛选控件槽(env/runtime 下拉等) */
  filters?: React.ReactNode;
  /** 即时搜索:受控值 */
  searchValue?: string;
  /** 即时搜索:值变化回调;不传则不渲染搜索框 */
  onSearchChange?: (value: string) => void;
  /** 搜索框占位符 */
  searchPlaceholder?: string;
  /** 刷新回调;不传则不渲染刷新按钮 */
  onRefresh?: () => void;
  /** 刷新进行中(转圈禁用) */
  refreshing?: boolean;
  /** 列显隐设置;不传则不渲染列设置入口 */
  columnSettings?: ColumnSettings;
  /** 右侧主操作按钮(新建/纳管等) */
  actions?: React.ReactNode;
}

export function TableToolbar({
  title,
  filters,
  searchValue,
  onSearchChange,
  searchPlaceholder = "搜索名称",
  onRefresh,
  refreshing = false,
  columnSettings,
  actions,
}: TableToolbarProps): React.ReactElement {
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
        {title != null && (
          <span style={{ fontSize: 15, fontWeight: 600, color: colors.textTitle }}>{title}</span>
        )}
        {filters}
      </Space>

      <Space size="small" wrap>
        {onSearchChange && (
          <Input
            size="small"
            allowClear
            prefix={<SearchOutlined style={{ color: colors.textPlaceholder }} />}
            placeholder={searchPlaceholder}
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
            style={{ width: 200 }}
          />
        )}
        {onRefresh && (
          <Tooltip title="刷新">
            <Button
              size="small"
              icon={<ReloadOutlined />}
              loading={refreshing}
              onClick={onRefresh}
            />
          </Tooltip>
        )}
        {columnSettings && (
          <Dropdown
            trigger={["click"]}
            popupRender={() => (
              <div
                style={{
                  background: colors.cardBg,
                  border: `1px solid ${colors.cardBorder}`,
                  borderRadius: 2,
                  boxShadow: "0 2px 8px rgba(0,0,0,.12)",
                  padding: "8px 12px",
                }}
              >
                {/* Checkbox.Group 承载多选:勾选只更新集合、不收起弹层,可连续勾选 */}
                <Checkbox.Group
                  value={columnSettings.value}
                  onChange={(checked) => columnSettings.onChange(checked as string[])}
                >
                  <Space direction="vertical" size={4}>
                    {columnSettings.options.map((col) => (
                      <Checkbox key={col.key} value={col.key}>
                        {col.label}
                      </Checkbox>
                    ))}
                  </Space>
                </Checkbox.Group>
              </div>
            )}
          >
            <Tooltip title="列设置">
              <Button size="small" icon={<SettingOutlined />} />
            </Tooltip>
          </Dropdown>
        )}
        {actions}
      </Space>
    </div>
  );
}
