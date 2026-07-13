"""Turn raw blog post HTML into structured internship data via the Claude API.

Uses client.messages.parse with a Pydantic schema so the response is
guaranteed to validate. Extraction failures are logged and return None;
the pipeline stores the post either way so nothing is lost.
"""

import hashlib
import logging

import anthropic
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 4096


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
    "The blog year context: posts are for the 2026-27 internship season."
)


def dedup_key(company: str | None, role: str | None, deadline: str | None) -> str:
    normalized = "|".join((v or "").strip().lower() for v in (company, role, deadline))
    return hashlib.sha256(normalized.encode()).hexdigest()


def extract_posting(
    client: anthropic.Anthropic, model: str, title: str, content_html: str, post_date: str
) -> InternshipExtraction | None:
    prompt = (
        f"Post title: {title}\n"
        f"Posted at (GMT): {post_date}\n"
        f"Post HTML content:\n{content_html}"
    )
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=InternshipExtraction,
        )
    except anthropic.RateLimitError:
        logger.warning("extraction rate limited for post %r", title)
        return None
    except anthropic.APIStatusError as e:
        logger.error("extraction API error %s for post %r: %s", e.status_code, title, e.message)
        return None
    except anthropic.APIConnectionError:
        logger.error("extraction network error for post %r", title)
        return None
    except Exception:
        logger.exception("unexpected extraction failure for post %r", title)
        return None

    if response.stop_reason == "refusal":
        logger.warning("extraction refused for post %r", title)
        return None
    return response.parsed_output
