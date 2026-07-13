"""service_configs 配置版本的边界 schema(§12/§14.5/§15.3)。

配置内容为文本快照,敏感值以 ${secret:名称} 占位符存储,不落明文密钥(§12.2)。
版本号按 service 自增(仓储层保证),is_current 标记当前生效版本。
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.config_delivery import DeliveryStatus
from app.models.service_config import ConfigFormat


class ConfigVersionCreate(BaseModel):
    """新建配置版本入参(§15.3 暂存)。content 允许空串(占位版本)。

    target_path 是下发时写到目标机的绝对路径(§12.2);不填则该版本只能查看/diff,
    apply 下发时会因缺路径而失败。
    """

    content: str = Field(default="", max_length=1_048_576)
    format: ConfigFormat = ConfigFormat.ENV
    comment: str | None = Field(default=None, max_length=512)
    target_path: str | None = Field(default=None, max_length=512)


class ConfigVersionOut(BaseModel):
    """配置版本视图(供版本历史与当前生效版展示)。"""

    id: str
    service_id: str
    version: int
    content: str
    format: ConfigFormat
    created_by: str | None = None
    comment: str | None = None
    target_path: str | None = None
    # 内容血缘(§14.5):content_hash 为内容 SHA-256(判等/跳过重复下发);
    # diff_from 指向本版派生的上一生效版 id(首版为 None),供版本溯源。
    content_hash: str = ""
    diff_from: str | None = None
    is_current: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ConfigDeliveryOut(BaseModel):
    """单个目标的下发结果视图(供下发页逐目标展示部分成功/失败)。"""

    id: str
    config_id: str
    placement_id: str
    status: DeliveryStatus
    result: str | None = None
    error: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
