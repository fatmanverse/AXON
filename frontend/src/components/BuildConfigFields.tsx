/**
 * 构建配置表单字段(块二:构建配置编写入口)。
 *
 * 可复用的一组 Form.Item,嵌进「新建服务」与「编辑构建配置」两处共用。按
 * artifact_type(generic|docker)联动显示形态专属字段:generic 显 output_path、
 * docker 显 image_ref + dockerfile。对齐后端 BuildConfigModel 的形态必填规则——
 * 前端做即时提示,后端提交时二次强校验(填错 key / 缺必填项 422)。
 *
 * 用法:嵌在父级 <Form> 内,字段名统一挂在 `build_config` 命名路径下,父级用
 * Form.List 语义的嵌套字段名(["build_config", "xxx"])收集,提交时组装成
 * BuildConfigInput。artifact_type 的联动用 Form.Item shouldUpdate 局部重渲染。
 */

import { Form, Input, Segmented } from "antd";
import type { FormInstance } from "antd";

import type { ArtifactType } from "@/api/services";

// build_config 各字段在表单里的命名前缀:与父级组装 BuildConfigInput 对齐。
const NS = "build_config";

interface BuildConfigFieldsProps {
  /** 父级 Form 实例,用于读取 artifact_type 做联动。 */
  form: FormInstance;
}

export function BuildConfigFields({ form }: BuildConfigFieldsProps): React.ReactElement {
  return (
    <>
      <Form.Item
        name={[NS, "repo_url"]}
        label="代码仓库"
        rules={[{ required: true, message: "请输入 git 仓库地址" }]}
        extra="控制面会 git clone 此仓库进行本地构建。"
      >
        <Input placeholder="如 https://git.example.com/team/app.git" />
      </Form.Item>
      <Form.Item name={[NS, "git_ref"]} label="默认分支 / 标签" extra="留空默认 main;触发构建时可覆盖。">
        <Input placeholder="如 main / v1.2.3" />
      </Form.Item>
      <Form.Item
        name={[NS, "test_command"]}
        label="测试命令"
        extra="构建前执行;留空则跳过测试。"
      >
        <Input placeholder="如 make test" />
      </Form.Item>
      <Form.Item
        name={[NS, "build_command"]}
        label="构建命令"
        rules={[{ required: true, message: "请输入构建命令" }]}
      >
        <Input placeholder="如 make build / npm run build" />
      </Form.Item>
      <Form.Item name={[NS, "artifact_type"]} label="制品形态" initialValue="generic">
        <Segmented
          options={[
            { label: "通用(tar 包)", value: "generic" },
            { label: "Docker 镜像", value: "docker" },
          ]}
        />
      </Form.Item>

      <Form.Item
        noStyle
        shouldUpdate={(prev, cur) =>
          prev?.[NS]?.artifact_type !== cur?.[NS]?.artifact_type
        }
      >
        {() => {
          const artifactType =
            (form.getFieldValue([NS, "artifact_type"]) as ArtifactType) ?? "generic";
          if (artifactType === "docker") {
            return (
              <>
                <Form.Item
                  name={[NS, "image_ref"]}
                  label="镜像坐标"
                  rules={[{ required: true, message: "Docker 形态必须提供镜像坐标" }]}
                  extra="构建后 docker build/push 到此坐标。"
                >
                  <Input placeholder="如 registry.example.com/team/app:1.0" />
                </Form.Item>
                <Form.Item
                  name={[NS, "dockerfile"]}
                  label="Dockerfile 路径"
                  extra="相对仓库根目录;留空默认 Dockerfile。"
                >
                  <Input placeholder="如 Dockerfile / docker/app.Dockerfile" />
                </Form.Item>
              </>
            );
          }
          return (
            <Form.Item
              name={[NS, "output_path"]}
              label="产物目录"
              rules={[{ required: true, message: "通用形态必须提供待打包的产物目录" }]}
              extra="仓库内待打包成 tar 的产物目录,相对仓库根。"
            >
              <Input placeholder="如 dist / build / target/release" />
            </Form.Item>
          );
        }}
      </Form.Item>
    </>
  );
}
