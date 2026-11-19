##############################################################################
# trialtrace/llm.py
#
# PURPOSE: The only place that talks to the model. It sends one prompt and returns
#          the text, the model name, the token counts and the time taken.
#          Every failure comes back as a result, never as a crash.
#          It knows nothing about trials: prompts and answer checks live elsewhere.
#
# USE:     result = call_llm("some prompt", settings)
##############################################################################

import time
from typing import Callable, Literal

import openai
from openai import OpenAI
from pydantic import BaseModel

from trialtrace.config import Settings

MAX_ATTEMPTS = 3  # One initial request and up to two retries.


class LLMResult(BaseModel):
    """The answer and usage details, or a failure code if no answer was obtained."""

    status: Literal["ok", "failed"]
    model: str
    text: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    seconds: float = 0.0
    attempts: int = 1
    error_code: str | None = None


def is_temporary(error: Exception) -> bool:
    """Timeouts, lost connections, rate limits and server errors may work on a retry."""
    if isinstance(error, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)):
        return True
    if isinstance(error, openai.APIStatusError):
        return error.status_code >= 500  # Server errors may be temporary.
    return False


def call_llm(
    prompt: str,
    settings: Settings,
    client: OpenAI | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> LLMResult:
    """Send one prompt. Retry temporary errors; return a failed result for anything else."""
    model = settings.model_name
    # 1. Use the supplied client (useful in tests), or create one with our settings.
    if client is None:
        if settings.openai_api_key is None:
            return LLMResult(status="failed", model=model, error_code="no_api_key")
        client = OpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=0,                                    # we do the retrying ourselves
        )

    started = time.monotonic()
    attempt = 1
    while True:
        # 2. Send the prompt. The SDK's own retries are disabled above.
        try:
            request = {"model": model, "messages": [{"role": "user", "content": prompt}]}
            if settings.llm_temperature is not None:
                request["temperature"] = settings.llm_temperature   # 0 = least random answers
            response = client.chat.completions.create(**request)
        except openai.OpenAIError as error:
            # 3. Retry temporary errors only, waiting 1 second and then 2 seconds.
            can_retry = is_temporary(error) and attempt < MAX_ATTEMPTS
            if can_retry:
                wait_seconds = 2 ** (attempt - 1)
                sleep(wait_seconds)
                attempt += 1
                continue
            seconds = round(time.monotonic() - started, 2)
            return LLMResult(
                status="failed",
                model=model,
                seconds=seconds,
                attempts=attempt,
                error_code=type(error).__name__,
            )

        seconds = round(time.monotonic() - started, 2)
        # 4. Read the first answer, if the response contains one.
        text = None
        if response.choices:
            text = response.choices[0].message.content
        if not text or not text.strip():
            return LLMResult(
                status="failed",
                model=response.model,
                seconds=seconds,
                attempts=attempt,
                error_code="empty_answer",
            )

        # 5. Keep token counts when provided; missing counts stay unknown.
        usage = response.usage
        input_tokens = None
        output_tokens = None
        if usage is not None:
            input_tokens = usage.prompt_tokens
            output_tokens = usage.completion_tokens
        return LLMResult(
            status="ok",
            model=response.model,
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            seconds=seconds,
            attempts=attempt,
        )
