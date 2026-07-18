/**
 * 表单弹窗:统一「Modal 包 Form」的表单交互范式(替代此前各页的抽屉表单)。
 *
 * 一处收口表单弹窗的通用约定,各页只管传 form 实例与业务字段:
 *   - layout="vertical" + requiredMark={false}:与全站表单排版一致
 *   - onOk 触发 form.submit(),交由父页 Form 的 onFinish 落业务(mutation)
 *   - confirmLoading 由父页的提交态驱动(mutation.isPending)
 *   - destroyOnClose:关闭即卸载,避免残留脏态
 *   - 取消时 resetFields:主动清空,与父页 onSuccess 的 resetFields 对称
 *
 * 设计取舍:不接管 form 生命周期(由父页 Form.useForm 持有),本组件只做壳与联动;
 * 业务字段作为 children 注入,校验/条件渲染逻辑仍留在父页 Form.Item,保持关注点分离。
 */

import { Form, Modal } from "antd";
import type { FormInstance } from "antd";

export interface FormModalProps<Values> {
  /** 弹窗标题 */
  title: React.ReactNode;
  /** 是否打开 */
  open: boolean;
  /** 父页持有的 form 实例(用于 submit / resetFields) */
  form: FormInstance<Values>;
  /** 表单提交回调(校验通过后触发,承载业务 mutation) */
  onFinish: (values: Values) => void;
  /** 关闭弹窗回调;组件会在触发前先 resetFields */
  onClose: () => void;
  /** 提交进行中(确认按钮转圈禁用) */
  confirmLoading?: boolean;
  /** 确认按钮文案 */
  okText?: React.ReactNode;
  /** 取消按钮文案 */
  cancelText?: string;
  /** 弹窗宽度,默认 480 */
  width?: number;
  /** 表单初始值 */
  initialValues?: Partial<Values>;
  /** 业务表单字段(Form.Item 集合) */
  children: React.ReactNode;
}

export function FormModal<Values extends object>({
  title,
  open,
  form,
  onFinish,
  onClose,
  confirmLoading = false,
  okText = "确定",
  cancelText = "取消",
  width = 480,
  initialValues,
  children,
}: FormModalProps<Values>): React.ReactElement {
  // 取消/关闭统一走此路径:先清空表单再回调父页,避免下次打开残留上次输入。
  const handleClose = (): void => {
    form.resetFields();
    onClose();
  };

  return (
    <Modal
      title={title}
      open={open}
      onOk={() => form.submit()}
      onCancel={handleClose}
      confirmLoading={confirmLoading}
      okText={okText}
      cancelText={cancelText}
      width={width}
      destroyOnClose
    >
      <Form<Values>
        form={form}
        layout="vertical"
        requiredMark={false}
        onFinish={onFinish}
        initialValues={initialValues}
      >
        {children}
      </Form>
    </Modal>
  );
}
