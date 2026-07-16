import type { ThemeConfig } from "antd";

/**
 * 一脉 Axon 自立视觉体系(2026-07 起不再对齐 JumpServer)。
 * 基调:现代运维控制台——深石板侧栏、靛蓝主色、灰白分层、克制阴影。
 * 原则:色值只在此文件定义,组件一律引用 colors 常量;间距用 spacing 尺度,
 * 禁止魔法数字;信息密度优先,装饰服务于可读性。
 */

export const colors = {
  // 主色:靛蓝(理性、技术感),hover/active 依次加深
  primary: "#4F63E6",
  primaryHover: "#4356CC",
  primaryActive: "#3947AD",
  // 极淡主色底:选中行/悬浮高亮/软标签
  primarySoft: "#EEF1FE",
  // 侧栏:深石板蓝黑,比纯黑柔和、比墨蓝更中性
  sidebarBg: "#1E2433",
  sidebarText: "#9AA3B5",
  sidebarActiveBg: "#2A3245",
  sidebarGroupTitle: "#5E6778",
  headerBorder: "#E9ECEF",
  contentBg: "#F5F6F8",
  cardBg: "#FFFFFF",
  cardBorder: "#E8EAED",
  splitLine: "#F0F1F3",
  textBody: "#4B5058",
  textTitle: "#1F2329",
  textMuted: "#9AA0A6",
  success: "#22A06B",
  warning: "#F0A11E",
  danger: "#E5484D",
  info: "#3B82F6",
  textPlaceholder: "#B0B3B5",
  neutral: "#8C8C8C",
  accentViolet: "#7D6BEE",
  chartViolet: "#9B59B6",
  chartSplitLine: "#F0F0F0",
  diffAddBg: "#F6FFED",
  diffAddText: "#237804",
  diffRemoveBg: "#FFF1F0",
  diffRemoveText: "#A8071A",
} as const;

// 间距尺度:页面/卡片/工具栏统一节奏,替代散落的 12/16/20 魔法数字。
export const spacing = {
  /** 工具栏与表格卡片之间 */
  toolbarGap: 16,
  /** 页面内区块纵向间隔 */
  sectionGap: 16,
  /** 卡片内边距 */
  cardPadding: 20,
} as const;

// 系统无衬线字体栈:不引入 Google Fonts(规范硬约束)。中文回退 PingFang / 雅黑。
const fontStack =
  '-apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, ' +
  '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "微软雅黑", Roboto, sans-serif';

// 柔性阴影分层:卡片静态用极淡,悬浮/弹层稍强。禁发光/彩色阴影。
const shadowCard = "0 1px 2px rgba(16,24,40,.04), 0 1px 3px rgba(16,24,40,.06)";
const shadowRaise = "0 4px 12px rgba(16,24,40,.08), 0 2px 4px rgba(16,24,40,.04)";

export const shadows = { card: shadowCard, raise: shadowRaise } as const;

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: colors.primary,
    colorPrimaryHover: colors.primaryHover,
    colorPrimaryActive: colors.primaryActive,
    colorSuccess: colors.success,
    colorWarning: colors.warning,
    colorError: colors.danger,
    colorInfo: colors.info,
    colorBgLayout: colors.contentBg,
    colorText: colors.textBody,
    colorTextHeading: colors.textTitle,
    colorTextDescription: colors.textMuted,
    colorBorder: colors.cardBorder,
    colorBorderSecondary: colors.splitLine,
    borderRadius: 6,
    borderRadiusLG: 8,
    borderRadiusSM: 4,
    fontSize: 14,
    lineHeight: 1.5715,
    fontFamily: fontStack,
    controlHeight: 34,
    boxShadow: shadowCard,
    boxShadowSecondary: shadowRaise,
    wireframe: false,
  },
  components: {
    Layout: {
      headerBg: colors.cardBg,
      headerHeight: 52,
      headerPadding: "0 20px",
      siderBg: colors.sidebarBg,
      bodyBg: colors.contentBg,
    },
    Menu: {
      darkItemBg: colors.sidebarBg,
      darkSubMenuItemBg: colors.sidebarActiveBg,
      darkItemColor: colors.sidebarText,
      darkItemSelectedBg: colors.sidebarActiveBg,
      darkItemSelectedColor: "#FFFFFF",
      darkItemHoverBg: "#252C3D",
      darkItemHoverColor: "#FFFFFF",
      darkGroupTitleColor: colors.sidebarGroupTitle,
      itemHeight: 42,
      itemMarginInline: 8,
      itemMarginBlock: 2,
      itemBorderRadius: 6,
      iconSize: 15,
    },
    Card: {
      colorBorderSecondary: colors.cardBorder,
      paddingLG: spacing.cardPadding,
      borderRadiusLG: 8,
      boxShadowTertiary: shadowCard,
    },
    Table: {
      headerBg: "#FAFBFC",
      headerColor: colors.textTitle,
      headerSplitColor: "transparent",
      borderColor: colors.splitLine,
      rowHoverBg: colors.primarySoft,
      cellPaddingBlock: 11,
      cellPaddingInline: 14,
    },
    Button: {
      primaryShadow: "none",
      defaultShadow: "none",
      fontWeight: 500,
    },
    Input: { paddingBlock: 5 },
    Select: { controlHeight: 34 },
    Modal: { borderRadiusLG: 10 },
    Tag: { borderRadiusSM: 4 },
    Segmented: { borderRadius: 6, trackBg: "#EEF0F3" },
  },
};
