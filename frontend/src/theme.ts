import type { ThemeConfig } from "antd";

/**
 * AntD 主题定制,对齐《前端设计规范》并做现代化收敛。
 * 基调不变:绿是点缀色、深墨蓝灰侧栏、中性灰白主导、信息密度高。
 * 现代化三处:圆角 6px(比 2px 柔和,仍远小于规范禁止的 12px+ 泡泡角)、
 * 正文 14px(比 13px 更透气)、层次靠"淡边框 + 柔性阴影"而非硬边框硬撑。
 */

export const colors = {
  primary: "#1AB394",
  primaryHover: "#159C82",
  primaryActive: "#128472",
  // 极淡主色底:用于选中行/悬浮高亮,替代生硬的灰底
  primarySoft: "#E9F7F3",
  sidebarBg: "#2F4050",
  sidebarText: "#A7B1C2",
  sidebarActiveBg: "#293846",
  sidebarGroupTitle: "#68757C",
  headerBorder: "#E9ECEF",
  contentBg: "#F4F5F7",
  cardBg: "#FFFFFF",
  cardBorder: "#E8EAED",
  // 更淡的分隔线,用于表格行/描述项等内部切分
  splitLine: "#F0F1F3",
  textBody: "#5A5E62",
  textTitle: "#2A2D31",
  textMuted: "#9AA0A6",
  success: "#1AB394",
  warning: "#F8AC59",
  danger: "#ED5565",
  info: "#1C84C6",
  // 表格空值占位("—"):比 textMuted 略浅,专用于"无数据"的弱提示
  textPlaceholder: "#B0B3B5",
  // 中性灰:已回滚 / 待下发等"非成功非失败"的中性终态
  neutral: "#8C8C8C",
  // 装饰强调紫:仪表盘 KPI 瓦片"放置点"的非语义强调色
  accentViolet: "#7D6BEE",
  // 图表第 5 序列色 + 网格分割线(ECharts 折线卡用)
  chartViolet: "#9B59B6",
  chartSplitLine: "#F0F0F0",
  // 配置 diff 行底色/文字:比语义 success/danger 更淡的行内高亮,专用于逐行 diff
  diffAddBg: "#F6FFED",
  diffAddText: "#237804",
  diffRemoveBg: "#FFF1F0",
  diffRemoveText: "#A8071A",
} as const;

// 系统无衬线字体栈:不引入 Google Fonts(规范硬约束)。中文回退 PingFang / 雅黑。
const fontStack =
  '"Helvetica Neue", Helvetica, Arial, "PingFang SC", "Hiragino Sans GB", ' +
  '"Microsoft YaHei", "微软雅黑", Roboto, -apple-system, BlinkMacSystemFont, sans-serif';

// 柔性阴影分层:卡片静态用极淡,悬浮/弹层用稍强。避免发光/彩色阴影(规范禁项)。
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
      darkItemHoverBg: "#26313D",
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
      paddingLG: 20,
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
