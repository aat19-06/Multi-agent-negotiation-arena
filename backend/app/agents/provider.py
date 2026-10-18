import asyncio
import logging

from app.agents.errors import AIProviderError
from app.core.config import settings
from app.core.limits import ai_request_gate

logger = logging.getLogger(__name__)


async def invoke_chat_model(client, messages):
    if not ai_request_gate.try_acquire():
        raise AIProviderError(429, "AI capacity is busy. Please try again shortly.")

    try:
        async with asyncio.timeout(settings.AI_REQUEST_TIMEOUT_SECONDS):
            return await client.ainvoke(messages)
    except TimeoutError as exc:
        raise AIProviderError(503, "The AI provider timed out. Please try again later.") from exc
    except Exception as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            detail = "The AI provider rate limit or free quota has been reached. Please try again later."
            response_status = 429
        else:
            detail = "The AI provider is unavailable. Please try again later."
            response_status = 503
        logger.warning("AI provider request failed (%s).", type(exc).__name__)
        raise AIProviderError(response_status, detail) from exc
    finally:
        ai_request_gate.release()
