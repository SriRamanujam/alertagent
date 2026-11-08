from typing import Optional

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI

from ..config import get_settings


class Model:
    _client: Optional[AsyncOpenAI] = None
    _model: Optional[OpenAIChatModel] = None

    @classmethod
    def get(cls) -> OpenAIChatModel:
        """
        Lazily initializes and returns usable model connection
        """
        if cls._model is not None:
            return cls._model

        settings = get_settings()
        if cls._client is None:
            cls._client = AsyncOpenAI(
                base_url=settings.openai_base_url, api_key=settings.openai_api_key
            )

        cls._model = OpenAIChatModel(
            model_name=settings.model_name,
            provider=OpenAIProvider(openai_client=cls._client),
            profile={"context_window": settings.context_size},
        )

        return cls._model

    @classmethod
    async def on_shutdown(cls) -> None:
        if cls._client is not None:
            await cls._client.close()

        cls._client = None
        cls._model = None
