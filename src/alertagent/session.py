import aiohttp

from typing import Optional

class SingletonAioHttp:
    aiohttp_client: Optional[aiohttp.ClientSession] = None

    @classmethod
    def get_client(cls) -> aiohttp.ClientSession:
        if cls.aiohttp_client is None:
            timeout = aiohttp.ClientTimeout(total=2)
            connector = aiohttp.TCPConnector(limit_per_host=100)
            cls.aiohttp_client = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return cls.aiohttp_client

    @classmethod
    async def close_client(cls) -> None:
        if cls.aiohttp_client:
            await cls.aiohttp_client.close()
            cls.aiohttp_client = None

async def on_startup() -> None:
    SingletonAioHttp.get_client()

async def on_shutdown() -> None:
    await SingletonAioHttp.close_client()