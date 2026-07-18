from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import urlsplit

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def validate_loopback_request(
    headers: Mapping[str, str],
    server_port: int,
    *,
    require_origin: bool = False,
) -> None:
    """Reject DNS-rebinding and cross-origin requests to a loopback control."""
    host = headers.get("Host", "")
    try:
        parsed_host = urlsplit(f"//{host}")
        host_port = parsed_host.port
    except ValueError as exc:
        raise PermissionError("invalid Host header") from exc
    if parsed_host.hostname not in LOOPBACK_HOSTS:
        raise PermissionError("loopback Host header required")
    if host_port is not None and host_port != server_port:
        raise PermissionError("Host port does not match the local control")

    origin = headers.get("Origin")
    if not origin:
        if require_origin:
            raise PermissionError("same-origin request required")
        return
    try:
        parsed_origin = urlsplit(origin)
        origin_port = parsed_origin.port or (80 if parsed_origin.scheme == "http" else 443)
    except ValueError as exc:
        raise PermissionError("invalid Origin header") from exc
    if (
        parsed_origin.scheme != "http"
        or parsed_origin.hostname not in LOOPBACK_HOSTS
        or origin_port != server_port
    ):
        raise PermissionError("same-origin loopback request required")
