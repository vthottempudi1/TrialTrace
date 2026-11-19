##############################################################################
# trialtrace/paper_reader.py
#
# PURPOSE: The FROZEN settings of the LLM that reads paper abstracts in the product,
#          in one place, so every stored prediction can say exactly which model,
#          which prompt and which schema produced it.
#          Provisional primary model: gpt-6.1-sol (chosen after the v3 and v4 evaluations,
#          see docs/DESIGN_DECISIONS.md D26o to D26r). gpt-5.5 stays a comparison model only.
#
# USE:     reader = reader_settings(Settings())     # settings for call_llm
#          info = reader_info(reader)               # what to store beside each prediction
##############################################################################

import hashlib
import json
import re

from typing import Callable
import time

from openai import OpenAI

from trialtrace.compare.abstract_llm import PROMPT
from trialtrace.config import Settings
from trialtrace.llm import call_llm

SCHEMA_VERSION = "paper-match-v2"        # the JSON the model must return (document, status, quote, name, role, mismatch)


def prompt_hash() -> str:
    """A short fingerprint of the prompt text: it changes whenever the prompt changes."""
    return hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()[:12]


def reader_settings(settings: Settings) -> Settings:
    """A copy of the settings with the frozen reader model and temperature. The caller's settings are not changed."""
    return settings.model_copy(update={
        "model_name": settings.paper_reader_model,
        "llm_temperature": settings.paper_reader_temperature,
    })


def reader_info(reader: Settings) -> dict:
    """What is stored beside every prediction, so a result can always be traced to its configuration."""
    return {
        "model": reader.model_name,
        "temperature": reader.llm_temperature,
        "prompt_hash": prompt_hash(),
        "schema_version": SCHEMA_VERSION,
    }


def llm_input_hash(measure_text: str, time_frame: str | None, abstract: str, info: dict, target_nct_id: str) -> str:
    """A fingerprint of everything the model sees and every setting that shapes its answer.

    The same fingerprint means the same question to the same configured model, so a stored answer can be reused.
    """
    if not isinstance(target_nct_id, str) or not re.fullmatch(r"NCT[0-9]{8}", target_nct_id):
        raise ValueError("A valid target NCT ID is required for the outcome cache key")
    parts = [info["model"], info["temperature"], info["prompt_hash"], info["schema_version"], measure_text, time_frame or "", abstract, target_nct_id]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def probe_served_model(reader: Settings, client: OpenAI | None = None, sleep: Callable[[float], None] = time.sleep) -> str | None:
    """Ask the model for a one-word answer (about 20 tokens) just to read which model the API reports it is serving.

    Returns the served model name, or None if the call failed (the caller decides what to do then).
    """
    result = call_llm("Reply with the single word OK.", reader, client=client, sleep=sleep)
    if result.status != "ok":
        return None
    return result.model
