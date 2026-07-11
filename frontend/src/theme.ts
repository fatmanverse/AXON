import type { ThemeConfig } from "antd";

/**
 * AntD 主题定制,严格对齐《前端设计规范》。
 * 关键:绿是点缀色、圆角 2px、字号 13px、浅灰内容底,避免一眼 antd 默认蓝。
 */

export const colors = {
  primary: "#1AB394",
  primaryHover: "#18A689",
  sidebarBg: "#2F4050",
  sidebarText: "#A7B1C2",
  sidebarActiveBg: "#293846",
  headerBorder: "#E7EAEC",
  contentBg: "#F3F3F4",
  cardBg: "#FFFFFF",
  cardBorder: "#E7EAEC",
  textBody: "#676A6C",
  textTitle: "#333333",
  success: "#1AB394",
  warning: "#F8AC59",
  danger: "#ED5565",
  info: "#1C84C6",
} as const;

const fontStack =
  '-apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif';

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: colors.primary,
    colorSuccess: colors.success,
    colorWarning: colors.warning,
    colorError: colors.danger,
    colorInfo: colors.info,
    colorBgLayout: colors.contentBg,
    colorText: colors.textBody,
    colorTextHeading: colors.textTitle,
    colorBorder: colors.cardBorder,
    borderRadius: 2,
    fontSize: 13,
    fontFamily: fontStack,
    boxShadow: "0 1px 2px rgba(0,0,0,.05)",
    wireframe: false,
  },
  components: {
    Layout: {
      headerBg: colors.cardBg,
      headerHeight: 48,
      headerPadding: "0 16px",
      siderBg: colors.sidebarBg,
      bodyBg: colors.contentBg,
    },
    Menu: {
      darkItemBg: colors.sidebarBg,
      darkSubMenuItemBg: colors.sidebarActiveBg,
      darkItemColor: colors.sidebarText,
      darkItemSelectedBg: colors.sidebarActiveBg,
      darkItemSelectedColor: "#FFFFFF",
      itemHeight: 40,
      iconSize: 14,
    },
    Card: {
      colorBorderSecondary: colors.cardBorder,
      paddingLG: 16,
    },
    Table: {
      headerBg: "#FAFAFA",
      headerColor: colors.textTitle,
      cellPaddingBlock: 8,
      cellPaddingInline: 12,
    },
    Button: {
      primaryShadow: "none",
      defaultShadow: "none",
    },
  },
};
