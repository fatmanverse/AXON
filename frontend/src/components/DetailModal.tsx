/**
 * 详情弹窗:统一「只读展示」的弹窗范式(替代此前的详情抽屉)。
 *
 * 与 FormModal 相对:无表单、无确认动作,footer 置空,只承载只读内容
 * (Descriptions / 表格 / 代码块等),点遮罩或右上角关闭即走 onClose。
 * destroyOnHidden 关闭即卸载,内容随目标切换重建,避免展示上一条的残影。
 *
 * 设计取舍:不内置 Descriptions——各详情的字段结构差异大(标签着色/预格式化
 * JSON 等),由父页以 children 注入,本组件只定壳的行为与尺寸基调。
 */

import { Modal } from "antd";

export interface DetailModalProps {
  /** 弹窗标题 */
  title: React.ReactNode;
  /** 是否打开 */
  open: boolean;
  /** 关闭回调(点遮罩/右上角关闭) */
  onClose: () => void;
  /** 弹窗宽度,默认 520 */
  width?: number;
  /** 只读内容槽 */
  children: React.ReactNode;
}

export function DetailModal({
  title,
  open,
  onClose,
  width = 520,
  children,
}: DetailModalProps): React.ReactElement {
  return (
    <Modal title={title} open={open} onCancel={onClose} footer={null} width={width} destroyOnHidden>
      {children}
    </Modal>
  );
}
