from unittest.mock import MagicMock

import pytest

from app.adapters.http_load_balancer import HttpLoadBalancer
from app.core.errors import AppError


class _Response:
    def raise_for_status(self) -> None:
        pass


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict]] = []

    async def post(self, url, *, json, headers):
        self.calls.append((url, json, headers))
        return _Response()


async def test_http_load_balancer_sends_weight_and_switch_requests():
    client = _Client()
    secrets = MagicMock()
    secrets.get.return_value = "token"
    lb = HttpLoadBalancer(
        "https://lb.internal/api",
        secrets,
        token_credential_id="lb-token",
        client=client,
    )

    await lb.set_weight("billing", 50)
    await lb.switch_upstream("billing", "green")

    assert client.calls == [
        (
            "https://lb.internal/api/weights",
            {"target": "billing", "weight": 50},
            {"Authorization": "Bearer token"},
        ),
        (
            "https://lb.internal/api/switch",
            {"target": "billing", "upstream": "green"},
            {"Authorization": "Bearer token"},
        ),
    ]


async def test_http_load_balancer_rejects_invalid_weight():
    lb = HttpLoadBalancer("https://lb.internal", MagicMock(), client=_Client())

    with pytest.raises(AppError) as exc:
        await lb.set_weight("billing", 101)

    assert exc.value.code == "load_balancer_invalid_weight"
