"""Joins roll numbers in scraped post tables (shortlists, mock-test results,
etc.) against the placement office's roll-list, adding a Department column so
readers don't have to cross-reference the roll list themselves.

The lookup itself lives in data/roll_departments.xlsx, built from the roll-
list PDF by scripts/build_roll_department_lookup.py (re-run that whenever the
placement office publishes an updated list). Matching is roll-number-first
since that's the unique, unambiguous key; a name-based fallback covers rows
where the roll number is missing, malformed, or not yet in the lookup (e.g. a
freshly admitted student). When a name matches more than one department, an
LLM is asked to pick from that closed set of candidates rather than left
blank - never allowed to invent a department outside what the lookup contains.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from html import escape

import openai

from app.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

LOOKUP_PATH = PROJECT_ROOT / "data" / "roll_departments.xlsx"

_ROLL_RE = re.compile(r"^\d{2}[A-Za-z]\d{3,5}$")
_ROW_RE = re.compile(r"<tr[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_HEADER_ROLL_RE = re.compile(r"roll\s*(no\.?|number)", re.IGNORECASE)

MAX_LLM_DISAMBIGUATION_CANDIDATES = 20


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower()).removeprefix("ms. ").removeprefix("mr. ")


@dataclass(frozen=True)
class RollDepartmentMaps:
    by_roll: dict[str, str] = field(default_factory=dict)
    by_name: dict[str, list[tuple[str, str]]] = field(default_factory=dict)


@lru_cache(maxsize=1)
def load_roll_department_maps() -> RollDepartmentMaps:
    if not LOOKUP_PATH.exists():
        logger.warning("roll-department lookup not found at %s, skipping department join", LOOKUP_PATH)
        return RollDepartmentMaps()

    import openpyxl

    wb = openpyxl.load_workbook(LOOKUP_PATH, read_only=True, data_only=True)
    sheet = wb.active
    rows = sheet.iter_rows(values_only=True)
    header = [str(c or "").strip().lower() for c in next(rows)]
    try:
        roll_idx, name_idx, dept_idx = header.index("roll_number"), header.index("name"), header.index("department")
    except ValueError:
        logger.error("roll-department lookup missing expected columns, got %r", header)
        return RollDepartmentMaps()

    by_roll: dict[str, str] = {}
    by_name: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        roll, name, department = row[roll_idx], row[name_idx], row[dept_idx]
        if not roll or not department:
            continue
        roll = str(roll).strip().upper()
        department = str(department).strip()
        by_roll[roll] = department
        if name:
            by_name.setdefault(_normalize_name(str(name)), []).append((roll, department))
    return RollDepartmentMaps(by_roll=by_roll, by_name=by_name)


def _cell_text(cell_html: str) -> str:
    return _TAG_RE.sub("", cell_html).strip()


@dataclass
class _RowMatch:
    row_html: str
    department: str | None
    # Set only when the row matched a name to multiple distinct departments -
    # left for the LLM disambiguation pass, since roll-number and unique-name
    # matches are already resolved by the time this is populated.
    ambiguous_name: str | None = None
    ambiguous_candidates: tuple[str, ...] = ()


def _match_row(row_html: str, maps: RollDepartmentMaps) -> _RowMatch:
    cells = [_cell_text(c) for c in _CELL_RE.findall(row_html)]
    if not cells:
        return _RowMatch(row_html, department=None)
    if any(_HEADER_ROLL_RE.search(c) for c in cells):
        return _RowMatch(row_html, department="Department")  # header marker, see annotate_roll_departments

    for cell in cells:
        if _ROLL_RE.match(cell):
            department = maps.by_roll.get(cell.upper())
            if department:
                return _RowMatch(row_html, department=department)

    for cell in cells:
        candidates = maps.by_name.get(_normalize_name(cell), [])
        if not candidates:
            continue
        distinct = sorted({d for _, d in candidates})
        if len(distinct) == 1:
            return _RowMatch(row_html, department=distinct[0])
        return _RowMatch(row_html, department=None, ambiguous_name=cell, ambiguous_candidates=tuple(distinct))

    return _RowMatch(row_html, department=None)


def _append_cell(row_html: str, tag: str, text: str) -> str:
    return row_html[: row_html.rindex("</tr>")] + f"<{tag}>{escape(text)}</{tag}></tr>"


def _llm_disambiguate(
    attempts: list[tuple[openai.OpenAI, str]], ambiguous: list[tuple[str, tuple[str, ...]]]
) -> dict[str, str]:
    """Resolves name -> department only from that name's own candidate list -
    the prompt supplies nothing else the model could substitute in, so a
    hallucinated department simply won't match anything we apply."""
    if not attempts or not ambiguous:
        return {}
    prompt = (
        "Each entry below is a student name that matches multiple departments in our roll list "
        "(same name shared by students in different departments). Without more context we cannot "
        "tell them apart with certainty, so just pick the most likely single department for each "
        "name from ITS OWN candidate list. Respond with a JSON object mapping each name exactly as "
        "given to one of its candidate department strings.\n\n"
        + json.dumps({name: list(candidates) for name, candidates in ambiguous})
    )
    for client, model in attempts:
        try:
            response = client.chat.completions.create(
                model=model,
                max_tokens=1024,
                response_format={"type": "json_object"},
                messages=[{"role": "user", "content": prompt}],
            )
            raw = (response.choices[0].message.content or "").strip()
            picked = json.loads(raw)
        except Exception:
            logger.exception("LLM department disambiguation failed on model %r", model)
            continue
        candidate_sets = dict(ambiguous)
        return {
            name: dept
            for name, dept in picked.items()
            if name in candidate_sets and dept in candidate_sets[name]
        }
    return {}


def annotate_roll_departments(html: str, attempts: list[tuple[openai.OpenAI, str]] | None = None) -> str:
    """Adds a Department column to any table in ``html`` whose rows contain
    roll numbers (or names) recognized from the roll list. Returns ``html``
    unchanged if there's no table or nothing in it matches - this only
    enriches, it never removes or alters existing content."""
    maps = load_roll_department_maps()
    if not maps.by_roll or "<table" not in html.lower() or "<th>Department</th>" in html:
        return html

    matches = [_match_row(m.group(0), maps) for m in _ROW_RE.finditer(html)]
    if not any(m.department or m.ambiguous_name for m in matches):
        return html

    ambiguous = [(m.ambiguous_name, m.ambiguous_candidates) for m in matches if m.ambiguous_name]
    if len(ambiguous) > MAX_LLM_DISAMBIGUATION_CANDIDATES:
        logger.warning("too many ambiguous name matches (%d) in one post, skipping LLM disambiguation", len(ambiguous))
        resolved: dict[str, str] = {}
    else:
        resolved = _llm_disambiguate(attempts or [], ambiguous)

    row_iter = iter(matches)

    def replace(match: re.Match) -> str:
        row_match = next(row_iter)
        if row_match.department == "Department":
            return _append_cell(row_match.row_html, "th", "Department")
        department = row_match.department or (
            resolved.get(row_match.ambiguous_name) if row_match.ambiguous_name else None
        )
        return _append_cell(row_match.row_html, "td", department) if department else row_match.row_html

    return _ROW_RE.sub(replace, html)
