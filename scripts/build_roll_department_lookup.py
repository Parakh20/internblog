"""Convert the placement office's roll-list PDF into data/roll_departments.xlsx,
the lookup app/roll_lookup.py joins against to add a Department column to
shortlist/result posts.

The PDF (one row per student) is organized as repeated sections:

    Program : B.Tech.
    Department : Aerospace Engineering
    Batch Year : 2024
     1.   24B0001   Roumya Ranjan Nayak   Div: D2   Tutorial: T7   Lab : P7
     ...

Re-run this whenever the placement office publishes an updated roll list.

Usage:
    python scripts/build_roll_department_lookup.py path/to/Roll_List.pdf
"""

import re
import subprocess
import sys
from pathlib import Path

import openpyxl

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = PROJECT_ROOT / "data" / "roll_departments.xlsx"

_PROGRAM_RE = re.compile(r"^\s*Program\s*:\s*(.+?)\s*$")
_DEPARTMENT_RE = re.compile(r"^\s*Department\s*:\s*(.+?)\s*$")
_BATCH_YEAR_RE = re.compile(r"^\s*Batch Year\s*:\s*(\d{4})\s*$")
_ROW_RE = re.compile(r"^\s*\d+\.\s+(?P<roll>\S+)\s+(?P<name>.+?)\s*Div\s*:", re.IGNORECASE)


def extract_text(pdf_path: Path) -> str:
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True, check=True, text=True,
    )
    return result.stdout


def parse_roll_departments(text: str) -> list[tuple[str, str, str, str, str]]:
    """Returns (roll_number, name, department, program, batch_year) rows."""
    rows: list[tuple[str, str, str, str, str]] = []
    program = department = batch_year = ""
    for line in text.splitlines():
        if m := _PROGRAM_RE.match(line):
            program = m.group(1)
            continue
        if m := _DEPARTMENT_RE.match(line):
            department = m.group(1)
            continue
        if m := _BATCH_YEAR_RE.match(line):
            batch_year = m.group(1)
            continue
        if m := _ROW_RE.match(line):
            rows.append((m.group("roll").strip().upper(), m.group("name").strip(), department, program, batch_year))
    return rows


def write_workbook(rows: list[tuple[str, str, str, str, str]], output_path: Path) -> None:
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "roll_departments"
    sheet.append(["roll_number", "name", "department", "program", "batch_year"])
    for row in rows:
        sheet.append(list(row))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: python {sys.argv[0]} path/to/Roll_List.pdf", file=sys.stderr)
        return 1
    pdf_path = Path(sys.argv[1])
    if not pdf_path.exists():
        print(f"PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    rows = parse_roll_departments(extract_text(pdf_path))
    if not rows:
        print("No roll-list rows parsed - check the PDF layout hasn't changed", file=sys.stderr)
        return 1

    write_workbook(rows, OUTPUT_PATH)
    departments = sorted({row[2] for row in rows})
    print(f"Wrote {len(rows)} rows across {len(departments)} departments to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
