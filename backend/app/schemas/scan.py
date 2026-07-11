"""scan_results 的边界 schema(§7.1)。"""

from pydantic import BaseModel, Field

from app.models.scan_result import Scanner


class ScanWebhookPayload(BaseModel):
    """扫描器上报的扫描结果(§7.1)。幂等键 (git_sha, scanner)。"""

    service: str = Field(min_length=1, max_length=128)
    git_sha: str = Field(min_length=1, max_length=64)
    scanner: Scanner
    critical: int = Field(default=0, ge=0)
    high: int = Field(default=0, ge=0)
    medium: int = Field(default=0, ge=0)
    low: int = Field(default=0, ge=0)
    passed: bool = False
    report_url: str | None = Field(default=None, max_length=512)


class ScanResultOut(BaseModel):
    """扫描结果视图(供门禁查询与详情展示)。"""

    id: str
    service: str
    git_sha: str
    scanner: Scanner
    critical: int
    high: int
    medium: int
    low: int
    passed: bool
    report_url: str | None = None

    model_config = {"from_attributes": True}
