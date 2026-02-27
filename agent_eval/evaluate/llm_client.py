"""
LLM API client factory and provider implementations.
"""

import logging
from abc import ABC, abstractmethod
from typing import Optional

from .exceptions import APIError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseAPIClient(ABC):
    """Base class for API clients."""

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.base_url = base_url.strip() if base_url and base_url.strip() else None

    @abstractmethod
    def call(
        self,
        prompt: str,
        model: str,
        system_message: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Make an API call and return the response text."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# OpenAI-compatible client
# ---------------------------------------------------------------------------

class OpenAIClient(BaseAPIClient):
    """OpenAI API client (also works for DeepSeek and other compatible APIs)."""

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        try:
            import openai  # noqa: F811
        except ImportError:
            raise ImportError(
                "openai package not installed. Install with: pip install openai"
            )

        super().__init__(api_key, base_url)

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        self.client = openai.OpenAI(**client_kwargs)
        logger.debug("Initialized OpenAI client")

    def call(
        self,
        prompt: str,
        model: str,
        system_message: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> str:
        if system_message is None:
            system_message = (
                "You are a strict, detail-oriented code review judge for "
                "software-engineering patches. Always respond with valid JSON."
            )

        try:
            logger.info("Calling OpenAI API with model: %s", model)
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )

            if not response.choices:
                raise APIError("No choices in response from OpenAI API")

            content = response.choices[0].message.content
            if not content:
                raise APIError("Empty content in response from OpenAI API")

            logger.debug("Received response from OpenAI API (%d chars)", len(content))
            return content
        except APIError:
            raise
        except Exception as e:
            logger.error("Error calling OpenAI API: %s", e, exc_info=True)
            raise APIError(f"Error calling OpenAI API: {e}") from e


# ---------------------------------------------------------------------------
# Anthropic client
# ---------------------------------------------------------------------------

class AnthropicClient(BaseAPIClient):
    """Anthropic API client."""

    def __init__(self, api_key: str, base_url: Optional[str] = None):
        try:
            import anthropic  # noqa: F811
        except ImportError:
            raise ImportError(
                "anthropic package not installed. Install with: pip install anthropic"
            )

        super().__init__(api_key, base_url)

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        self.client = anthropic.Anthropic(**client_kwargs)
        logger.debug("Initialized Anthropic client")

    def call(
        self,
        prompt: str,
        model: str,
        system_message: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: Optional[int] = None,
    ) -> str:
        if system_message is None:
            system_message = (
                "You are a strict, detail-oriented code review judge for "
                "software-engineering patches. Always respond with valid JSON."
            )

        if max_tokens is None:
            max_tokens = 4096

        try:
            logger.info("Calling Anthropic API with model: %s", model)
            message = self.client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_message,
                messages=[{"role": "user", "content": prompt}],
            )

            if not message.content or len(message.content) == 0:
                raise APIError("No content in response from Anthropic API")

            content_block = message.content[0]
            if hasattr(content_block, "text"):
                content = content_block.text
            else:
                content = str(content_block)

            if not content:
                raise APIError("Empty content in response from Anthropic API")

            logger.debug("Received response from Anthropic API (%d chars)", len(content))
            return content
        except APIError:
            raise
        except Exception as e:
            logger.error("Error calling Anthropic API: %s", e, exc_info=True)
            raise APIError(f"Error calling Anthropic API: {e}") from e


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_api_client(
    model_name: str,
    api_key: str,
    base_url: Optional[str] = None,
    provider: Optional[str] = None,
) -> BaseAPIClient:
    """Return the appropriate API client.

    If *provider* is given (``"openai"`` or ``"anthropic"``), it takes
    precedence.  Otherwise the provider is inferred from *model_name*:
    ``gpt-*``, ``o1-*``, ``deepseek-*`` → :class:`OpenAIClient`;
    ``claude-*`` → :class:`AnthropicClient`; unknown → OpenAI.
    """
    if not isinstance(model_name, str):
        raise ValueError(
            f"model_name must be a string, got {type(model_name).__name__}: {model_name!r}"
        )

    if provider is not None and not isinstance(provider, str):
        raise ValueError(
            f"provider must be a string or None, got {type(provider).__name__}: {provider!r}"
        )

    if provider:
        p = provider.strip().lower()
        if p == "anthropic":
            logger.debug("Using Anthropic client (explicit provider) for model: %s", model_name)
            return AnthropicClient(api_key, base_url)
        if p == "openai":
            logger.debug("Using OpenAI client (explicit provider) for model: %s", model_name)
            return OpenAIClient(api_key, base_url)
        raise ValueError(
            f"Invalid provider={provider!r}. Must be 'openai' or 'anthropic'."
        )

    model_lower = model_name.lower()

    if model_lower.startswith(("gpt-", "o1-", "deepseek-")):
        logger.debug("Using OpenAI client for model: %s", model_name)
        return OpenAIClient(api_key, base_url)
    elif model_lower.startswith("claude-"):
        logger.debug("Using Anthropic client for model: %s", model_name)
        return AnthropicClient(api_key, base_url)
    else:
        logger.warning("Unknown model provider for %s, defaulting to OpenAI", model_name)
        return OpenAIClient(api_key, base_url)
