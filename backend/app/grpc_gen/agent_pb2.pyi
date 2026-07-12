from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class AckKind(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ACK_KIND_UNSPECIFIED: _ClassVar[AckKind]
    ACK_KIND_RECEIVED: _ClassVar[AckKind]
    ACK_KIND_RESULT: _ClassVar[AckKind]
ACK_KIND_UNSPECIFIED: AckKind
ACK_KIND_RECEIVED: AckKind
ACK_KIND_RESULT: AckKind

class AgentMessage(_message.Message):
    __slots__ = ("agent_id", "heartbeat", "status", "ack")
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    STATUS_FIELD_NUMBER: _ClassVar[int]
    ACK_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    heartbeat: Heartbeat
    status: ServiceStatus
    ack: CommandAck
    def __init__(self, agent_id: _Optional[str] = ..., heartbeat: _Optional[_Union[Heartbeat, _Mapping]] = ..., status: _Optional[_Union[ServiceStatus, _Mapping]] = ..., ack: _Optional[_Union[CommandAck, _Mapping]] = ...) -> None: ...

class Heartbeat(_message.Message):
    __slots__ = ("agent_version",)
    AGENT_VERSION_FIELD_NUMBER: _ClassVar[int]
    agent_version: str
    def __init__(self, agent_version: _Optional[str] = ...) -> None: ...

class ServiceStatus(_message.Message):
    __slots__ = ("service_ref", "running", "detail")
    SERVICE_REF_FIELD_NUMBER: _ClassVar[int]
    RUNNING_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    service_ref: str
    running: bool
    detail: str
    def __init__(self, service_ref: _Optional[str] = ..., running: _Optional[bool] = ..., detail: _Optional[str] = ...) -> None: ...

class CommandAck(_message.Message):
    __slots__ = ("task_id", "kind", "ok", "detail")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    KIND_FIELD_NUMBER: _ClassVar[int]
    OK_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    kind: AckKind
    ok: bool
    detail: str
    def __init__(self, task_id: _Optional[str] = ..., kind: _Optional[_Union[AckKind, str]] = ..., ok: _Optional[bool] = ..., detail: _Optional[str] = ...) -> None: ...

class ServerCommand(_message.Message):
    __slots__ = ("task_id", "action", "params", "fence")
    class ParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ACTION_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    FENCE_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    action: str
    params: _containers.ScalarMap[str, str]
    fence: int
    def __init__(self, task_id: _Optional[str] = ..., action: _Optional[str] = ..., params: _Optional[_Mapping[str, str]] = ..., fence: _Optional[int] = ...) -> None: ...
