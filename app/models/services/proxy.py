import logging

import httpx
from fastapi import Request

from app.models.services.interfaces import ProxyBase

logger = logging.getLogger(__name__)

# Headers that must not be forwarded to the upstream
_HOP_BY_HOP = frozenset({
    "host", "connection", "transfer-encoding",
    "te", "trailer", "upgrade", "keep-alive",
    "proxy-authorization", "proxy-authenticate",
})


class HttpxProxyService(ProxyBase):
    """
    Forwards a FastAPI Request to the upstream URL using httpx.

    S — responsible only for faithfully replaying the request.
        It does not rate-limit, does not log, does not modify business logic.
    L — substitutable for ProxyBase; raises httpx.HTTPError on failure
        as documented by the contract.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def forward(self, request: Request, upstream_url: str) -> httpx.Response:
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        body = await request.body()

        logger.debug("Forwarding %s %s -> %s", request.method, request.url.path, upstream_url)

        response = await self._client.request(
            method=request.method,
            url=upstream_url,
            headers=headers,
            content=body,
            follow_redirects=True,
        )
        return response
