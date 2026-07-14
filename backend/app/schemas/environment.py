"""environments 的输入输出 schema(自定义环境管理)。"""

from pydantic import BaseModel, Field


class EnvironmentCreate(BaseModel):
    """创建环境入参。name 为稳定标识(唯一);requires_approval 决定该环境是否走审批流。"""

    name: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    requires_approval: bool = False
    description: str = Field(default="", max_length=512)


class EnvironmentOut(BaseModel):
    """环境响应视图。"""

    id: str
    name: str
    display_name: str
    requires_approval: bool
    description: str

    model_config = {"from_attributes": True}
