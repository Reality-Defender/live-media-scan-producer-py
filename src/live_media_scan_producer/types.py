from dataclasses import dataclass
from datetime import datetime
from enum import Enum


@dataclass(frozen=True)
class HelloLimits:
    max_chunk_size_bytes: int
    max_response_tout_ms: int
    max_media_tout_ms: int
    max_delay_ms: int


@dataclass(frozen=True)
class HelloPayload:
    version: int
    version_min: int
    limits: HelloLimits
    allowed_media: list[str]


@dataclass(frozen=True)
class Hello:
    payload: HelloPayload
    type: str = "notice"
    subtype: str = "hello"


@dataclass(frozen=True)
class SourceIds:
    phone_number: str
    display_name: str
    file_name: str
    email: str


@dataclass(frozen=True)
class Metadata:
    pass


@dataclass(frozen=True)
class Properties:
    direction: str
    session_type: str
    test: bool = False


class Reason(Enum):
    Normal = "Normal"
    Error = "Error"
    Timeout = "Timeout"


@dataclass(frozen=True)
class StartRequestPayload:
    bitrate: int
    analysis_channel: str
    primary_source_id: str
    source_ids: SourceIds
    metadata: Metadata
    properties: Properties


@dataclass(frozen=True)
class StartRequest:
    session_id: str
    media_type: str
    payload: StartRequestPayload
    type: str = "request"
    subtype: str = "start"


@dataclass(frozen=True)
class StartResponseStatus(Enum):
    SUCCESS = "success",
    FAIL = "fail"


@dataclass(frozen=True)
class StartResponseStatusSuccessPayload:
    stream_id: str


@dataclass(frozen=True)
class StartResponseStatusFailPayload:
    code: str
    reason: str


@dataclass(frozen=True)
class StartResponse:
    status: StartResponseStatus
    payload: StartResponseStatusSuccessPayload | StartResponseStatusFailPayload
    type: str = "response"
    subtype: str = "start"

    def __post_init__(self):
        if ((self.status == StartResponseStatus.SUCCESS and
             not isinstance(self.payload, StartResponseStatusSuccessPayload)) or
                (self.status == StartResponseStatus.FAIL and
                 not isinstance(self.payload, StartResponseStatusFailPayload))):
            raise TypeError(f"Payload does not match status: {self.payload}")


@dataclass(frozen=True)
class StopRequestPayload:
    reason: str


@dataclass(frozen=True)
class StopRequest:
    stream_id: str
    payload: StopRequestPayload
    type: str = "request"
    subtype: str = "stop"


class StopResponseStatus(Enum):
    SUCCESS = "success",
    FAIL = "fail"


@dataclass(frozen=True)
class StopResponseStatusSuccessPayload:
    stream_id: str
    total_bytes: int
    stream_start: datetime
    stream_stop: datetime


@dataclass(frozen=True)
class StopResponseStatusFailPayload:
    code: str
    reason: str


@dataclass(frozen=True)
class StopResponse:
    status: StopResponseStatus
    payload: StopResponseStatusSuccessPayload | StopResponseStatusFailPayload
    type: str = "response"
    subtype: str = "stop"

    def __post_init__(self):
        if ((self.status == StopResponseStatus.SUCCESS and
             not isinstance(self.payload, StopResponseStatusSuccessPayload)) or
                (self.status == StopResponseStatus.FAIL and
                 not isinstance(self.payload, StopResponseStatusFailPayload))):
            raise TypeError(f"Payload does not match status: {self.payload}")
