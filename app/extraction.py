"""Turn raw blog post HTML into structured internship data via an LLM.

Uses any OpenAI-compatible API (OpenRouter by default, Groq works with the
same code via LLM_BASE_URL). The model returns JSON which is validated with
Pydantic; validation failures are logged and return None. The pipeline
stores the post either way so nothing is lost.
"""

import hashlib
import json
import logging

import openai
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 2048
REQUEST_TIMEOUT = 60.0


class InternshipExtraction(BaseModel):
    company: str | None = Field(None, description="Company name, null if not a job posting")
    role: str | None = Field(None, description="Role or position title")
    deadline: str | None = Field(
        None, description="Application deadline as ISO 8601 datetime with timezone if stated, else null"
    )
    cgpa_cutoff: str | None = Field(None, description="CGPA cutoff if mentioned")
    eligible_branches: list[str] = Field(
        default_factory=list, description="Eligible branches or departments, empty if unrestricted or unstated"
    )
    stipend: str | None = Field(None, description="Stipend or compensation as stated")
    location: str | None = Field(None, description="Work location")
    application_link: str | None = Field(None, description="URL to apply, if present")
    is_job_posting: bool = Field(
        description="True if this post announces an internship or job opportunity, false for PPTs, results, or administrative notices"
    )
    summary: str = Field(description="One or two sentence factual summary of the post")


SYSTEM_PROMPT = (
    "You extract structured data from posts on an IIT Bombay internship blog. "
    "Posts may be job announcements, pre-placement talk notices, shortlist results, "
    "or administrative updates. Extract only what the post states. Never invent values. "
    "Assume dates without a timezone are Asia/Kolkata (IST, UTC+05:30). "
    "The blog year context: posts are for the 2026-27 internship season.\n\n"
    "Respond with a single JSON object matching this schema, no other text:\n"
    + json.dumps(InternshipExtraction.model_json_schema())
)


def dedup_key(company: str | None, role: str | None, deadline: str | None) -> str:
    normalized = "|".join((v or "").strip().lower() for v in (company, role, deadline))
    return hashlib.sha256(normalized.encode()).hexdigest()


def make_llm_client(base_url: str, api_key: str) -> openai.OpenAI:
    return openai.OpenAI(base_url=base_url, api_key=api_key, timeout=REQUEST_TIMEOUT)


def extract_posting(
    client: openai.OpenAI, model: str, title: str, content_html: str, post_date: str
) -> InternshipExtraction | None:
    prompt = (
        f"Post title: {title}\n"
        f"Posted at (GMT): {post_date}\n"
        f"Post HTML content:\n{content_html}"
    )
    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
    except openai.RateLimitError:
        logger.warning("extraction rate limited for post %r", title)
        return None
    except openai.APIStatusError as e:
        logger.error("extraction API error %s for post %r: %s", e.status_code, title, e.message)
        return None
    except openai.APIConnectionError:
        logger.error("extraction network error for post %r", title)
        return None
    except Exception:
        logger.exception("unexpected extraction failure for post %r", title)
        return None

    raw = response.choices[0].message.content or ""
    try:
        return InternshipExtraction.model_validate_json(raw)
    except ValidationError:
        logger.error("extraction returned invalid JSON for post %r: %s", title, raw[:500])
        return None
