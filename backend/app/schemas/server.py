"""servers 的输入输出 schema(§14.1)。"""

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.server import AccessMode, AgentStatus


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    access_mode: AccessMode
    ssh_credential_id: str | None = Field(default=None, max_length=128)
    agent_id: str | None = Field(default=None, max_length=128)
    agent_status: AgentStatus = AgentStatus.UNKNOWN
    agent_version: str | None = Field(default=None, max_length=64)
    labels: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_access_identity(self) -> "ServerCreate":
        if self.access_mode == AccessMode.SSH:
            if not self.ssh_credential_id or self.agent_id:
                raise ValueError("ssh 模式必须提供 ssh_credential_id，且不能提供 agent_id")
            return self
        if not self.agent_id or self.ssh_credential_id:
            raise ValueError("agent 模式必须提供 agent_id，且不能提供 ssh_credential_id")
        return self


class ServerRegisterRequest(BaseModel):
    """纳管 API 入参(§3.2)。

    与 ServerCreate 的区别:SSH 模式收**私钥明文**（ssh_private_key）而非
    credential_id——私钥由 API 层存入保险箱换取 credential_id，绝不落业务表（§13）。
    """

    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    access_mode: AccessMode
    # SSH 模式
    username: str | None = Field(default=None, max_length=64)
    ssh_private_key: str | None = Field(default=None)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    # Agent 模式
    agent_id: str | None = Field(default=None, max_length=128)
    labels: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_by_mode(self) -> "ServerRegisterRequest":
        if self.access_mode == AccessMode.SSH:
            # username 可选:不传时由路由用 root 兜底
            if not self.ssh_private_key:
                raise ValueError("ssh 模式必须提供 ssh_private_key")
            if self.agent_id:
                raise ValueError("ssh 模式不能提供 agent_id")
            return self
        if not self.agent_id:
            raise ValueError("agent 模式必须提供 agent_id")
        if self.ssh_private_key or self.username:
            raise ValueError("agent 模式不能提供 ssh 凭证")
        return self


class ServerOut(BaseModel):
    """服务器响应视图。绝不含私钥;credential_id 仅作引用暴露。"""

    id: str
    name: str
    host: str
    access_mode: AccessMode
    ssh_credential_id: str | None = None
    agent_id: str | None = None
    agent_status: AgentStatus
    agent_version: str | None = None
    labels: dict[str, Any]

    model_config = {"from_attributes": True}
