"""外部服务客户端。"""

from __future__ import annotations

from app.clients.algorithm_client import AlgorithmClient, get_algorithm_client, reset_algorithm_client
from app.clients.maas_client import MaasClient, get_maas_client, reset_maas_client

__all__ = [
    "AlgorithmClient",
    "get_algorithm_client",
    "reset_algorithm_client",
    "MaasClient",
    "get_maas_client",
    "reset_maas_client",
]
