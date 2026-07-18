"""Turn raw blog post HTML into structured internship data via an LLM.

Uses any OpenAI-compatible API. The pipeline builds a fallback chain of
(client, model) pairs, e.g. Groq's free tier first, then OpenRouter's free
models. The model returns JSON which is validated with Pydantic; validation
failures are logged and return None. The pipeline stores the post either way
so nothing is lost.
"""

import hashlib
import json
import logging
import re
from datetime import datetime
from enum import StrEnum

import openai
from pydantic import BaseModel, Field, ValidationError, ValidationInfo, field_validator, model_validator

logger = logging.getLogger(__name__)

MAX_OUTPUT_TOKENS = 3072
REQUEST_TIMEOUT = 60.0
PARSE_ATTEMPTS = 2

# The blog only covers this internship season; any extracted year below this
# is a model hallucination (seen: PPT/test dates coming back as 2025) rather
# than a real value, since nothing on the blog predates the season.
MIN_SEASON_YEAR = 2026

_TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")


def _normalize_datetime_str(raw: str | None, fallback_date: str | None) -> str | None:
    """Fix two observed model quirks: a bare time with no date (combine with
    the post's own posted-at date as the best available fallback), and a
    year below the season (clamp to MIN_SEASON_YEAR - the model sometimes
    writes last year's date out of habit). Leaves anything else untouched;
    genuinely unparseable values are caught later by downstream consumers."""
    if raw is None:
        return None
    candidate = raw.strip()
    if _TIME_ONLY_RE.match(candidate) and fallback_date:
        candidate = f"{fallback_date[:10]}T{candidate}"
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return raw
    if dt.year < MIN_SEASON_YEAR:
        dt = dt.replace(year=MIN_SEASON_YEAR)
    return dt.isoformat()


class PostCategory(StrEnum):
    NEW_LISTING = "new_listing"
    TEST_UPDATE = "test_update"
    TEST_RESCHEDULE = "test_reschedule"
    PPT = "ppt"
    SHORTLIST_RESULT = "shortlist_result"
    DEADLINE_EXTENSION = "deadline_extension"
    ADMINISTRATIVE = "administrative"
    OTHER = "other"


# Categories worth pushing a Telegram notification for. Deliberately excludes
# ADMINISTRATIVE/OTHER (registration reminders, generic office notices) so
# every post doesn't turn into a message - see docs/decisions.md.
NOTIFY_CATEGORIES = frozenset(
    {
        PostCategory.NEW_LISTING,
        PostCategory.TEST_UPDATE,
        PostCategory.TEST_RESCHEDULE,
        PostCategory.PPT,
        PostCategory.SHORTLIST_RESULT,
        PostCategory.DEADLINE_EXTENSION,
    }
)

# Categories worth a calendar entry when they carry a date. Excludes results
# (nothing to attend/act on by a date) and ADMINISTRATIVE/OTHER.
CALENDAR_CATEGORIES = frozenset(
    {
        PostCategory.NEW_LISTING,
        PostCategory.TEST_UPDATE,
        PostCategory.TEST_RESCHEDULE,
        PostCategory.PPT,
        PostCategory.DEADLINE_EXTENSION,
    }
)

# Groups categories that describe updates to the SAME underlying event for a
# company, so e.g. a deadline_extension modifies the original new_listing's
# calendar event in place instead of creating a second one. Categories in
# different groups for the same company (e.g. a PPT vs. the application
# deadline) stay as separate events - they're genuinely different occasions.
CALENDAR_EVENT_GROUP: dict[PostCategory, str] = {
    PostCategory.NEW_LISTING: "deadline",
    PostCategory.DEADLINE_EXTENSION: "deadline",
    PostCategory.TEST_UPDATE: "test",
    PostCategory.TEST_RESCHEDULE: "test",
    PostCategory.PPT: "ppt",
}

# Same idea for Telegram: a follow-up post about the same company and the
# same kind of event edits the earlier message in place instead of sending
# a duplicate. Superset of CALENDAR_EVENT_GROUP - shortlist_result isn't
# calendar-relevant (no date to act on) but still benefits from grouping.
NOTIFY_EVENT_GROUP: dict[PostCategory, str] = {
    **CALENDAR_EVENT_GROUP,
    PostCategory.SHORTLIST_RESULT: "result",
}


class InternshipExtraction(BaseModel):
    company: str | None = Field(None, description="Company name, null if not a job posting")
    role: str | None = Field(
        None, description="Role or position title. If multiple roles are offered, join them with ', '"
    )

    @field_validator("role", mode="before")
    @classmethod
    def _join_role_list(cls, value: object) -> object:
        """Models sometimes return multiple simultaneously-open roles as a
        list instead of following the join-with-comma instruction."""
        return ", ".join(str(v) for v in value) if isinstance(value, list) else value

    deadline: str | None = Field(
        None,
        description=(
            "The single most relevant date/deadline for this post as ISO 8601 datetime with "
            "timezone if stated, else null. For a job posting this is the application deadline; "
            "for a test/PPT notice this is the start of the test or talk; for a reschedule this "
            "is the new start date/time. This is a specific instant, not a range"
        ),
    )
    event_end: str | None = Field(
        None,
        description=(
            "End time as ISO 8601 datetime, ONLY if the post explicitly states a start-end time "
            "range for a test/OA window or PPT (e.g. 'test from 6:00 PM to 7:00 PM'). Null for "
            "application deadlines and for posts that only state a single time, not a range"
        ),
    )
    cgpa_cutoff: str | None = Field(None, description="CGPA cutoff if mentioned")
    eligible_branches: list[str] = Field(
        default_factory=list, description="Eligible branches or departments, empty if unrestricted or unstated"
    )

    @field_validator("eligible_branches", mode="before")
    @classmethod
    def _coerce_null_branches_to_empty_list(cls, value: object) -> object:
        """Models sometimes emit `null` here instead of `[]` when unstated,
        which would otherwise fail validation against list[str] and drop the
        entire extraction (silently, via the generic invalid-JSON path)."""
        return value if value is not None else []

    stipend: str | None = Field(None, description="Stipend or compensation as stated")
    location: str | None = Field(None, description="Work location")
    application_link: str | None = Field(
        None, description="URL to apply, if present. If multiple links are given, use the first"
    )

    @field_validator("application_link", mode="before")
    @classmethod
    def _first_application_link(cls, value: object) -> object:
        """Same list-instead-of-single-value pattern as role, for posts with
        multiple simultaneous application links."""
        return value[0] if isinstance(value, list) and value else value

    category: PostCategory = Field(
        description=(
            "What kind of post this is: 'new_listing' (a new internship/job opportunity), "
            "'test_update' (a test/OA date or details announced), "
            "'test_reschedule' (an existing test/OA date changed), "
            "'ppt' (pre-placement talk notice), "
            "'shortlist_result' (shortlist or selection result), "
            "'deadline_extension' (an application deadline pushed back), "
            "'administrative' (registration reminders, generic placement-office notices), "
            "'other' (anything that doesn't fit the above)"
        )
    )
    summary: str = Field(description="One or two sentence factual summary of the post")

    @field_validator("summary", mode="before")
    @classmethod
    def _coerce_null_summary_to_empty_string(cls, value: object) -> object:
        """Same null-for-unstated-field issue as eligible_branches, seen on
        posts the model considers to have nothing worth summarizing."""
        return value if value is not None else ""

    @model_validator(mode="after")
    def _fix_single_time_swapped_into_event_end(self) -> "InternshipExtraction":
        """Models sometimes put a post's only stated time into event_end and
        leave deadline null, even when the post has no start-end range - seen
        repeatedly with llama-3.1-8b-instant on test/PPT notices. deadline is
        what every downstream consumer (notifications, calendar) filters on,
        so recover it here rather than silently dropping the post."""
        if self.deadline is None and self.event_end is not None:
            self.deadline = self.event_end
            self.event_end = None
        return self

    @model_validator(mode="after")
    def _normalize_dates(self, info: ValidationInfo) -> "InternshipExtraction":
        fallback_date = (info.context or {}).get("post_date") if info.context else None
        self.deadline = _normalize_datetime_str(self.deadline, fallback_date)
        self.event_end = _normalize_datetime_str(self.event_end, fallback_date)
        return self


SYSTEM_PROMPT = (
    "You extract structured data from posts on an IIT Bombay internship blog. "
    "Posts may be job announcements, pre-placement talk notices, test/OA schedule updates "
    "or reschedules, shortlist results, deadline extensions, or administrative updates. "
    "Extract only what the post states. Never invent values. "
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
    attempts: list[tuple[openai.OpenAI, str]], title: str, content_html: str, post_date: str
) -> InternshipExtraction | None:
    """Try each (client, model) pair in order, falling through to the next one
    when a model is rate limited, pulled from the free tier, or otherwise
    unavailable. Within a model, retry once on invalid JSON output.

    ``attempts`` lets each entry point at a different provider (e.g. Groq's
    own free tier first, then OpenRouter's free-model chain as fallback),
    since a single provider going down shouldn't stop extraction."""
    prompt = (
        f"Post title: {title}\n"
        f"Posted at (GMT): {post_date}\n"
        f"Post HTML content:\n{content_html}"
    )
    for client, model in attempts:
        result = _extract_with_model(client, model, prompt, title, post_date)
        if result is UNAVAILABLE:
            logger.warning("model %r unavailable for post %r, trying next model", model, title)
            continue
        return result
    logger.error("all fallback models exhausted for post %r", title)
    return None


UNAVAILABLE = object()


def _extract_with_model(
    client: openai.OpenAI, model: str, prompt: str, title: str, post_date: str
) -> InternshipExtraction | None | object:
    for attempt in range(1, PARSE_ATTEMPTS + 1):
        parsed = _attempt_extraction(client, model, prompt, title, post_date)
        if parsed is UNAVAILABLE:
            return UNAVAILABLE
        if parsed is not None:
            return parsed
        if attempt < PARSE_ATTEMPTS:
            logger.info("retrying extraction for post %r (attempt %d)", title, attempt + 1)
    return None


def _attempt_extraction(
    client: openai.OpenAI, model: str, prompt: str, title: str, post_date: str
) -> InternshipExtraction | None | object:
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
        logger.warning("extraction rate limited for post %r on model %r", title, model)
        return UNAVAILABLE
    except openai.APIStatusError as e:
        logger.error("extraction API error %s for post %r on model %r: %s", e.status_code, title, model, e.message)
        return UNAVAILABLE if e.status_code == 404 else None
    except openai.APIConnectionError:
        logger.error("extraction network error for post %r on model %r", title, model)
        return None
    except Exception:
        logger.exception("unexpected extraction failure for post %r on model %r", title, model)
        return None

    raw = (response.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.removeprefix("json").strip()
    try:
        return InternshipExtraction.model_validate_json(raw, context={"post_date": post_date})
    except ValidationError:
        logger.error("extraction returned invalid JSON for post %r: %s", title, raw[:500])
        return None
