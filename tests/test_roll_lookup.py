import json

import openpyxl
import pytest

from app import roll_lookup
from app.roll_lookup import annotate_roll_departments, load_roll_department_maps


@pytest.fixture(autouse=True)
def _lookup_workbook(tmp_path, monkeypatch):
    path = tmp_path / "roll_departments.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.append(["roll_number", "name", "department", "program", "batch_year"])
    sheet.append(["24B0945", "Bhavesh Ramakrishnan Karthik", "Computer Science and Engineering", "B.Tech.", "2024"])
    sheet.append(["24B1223", "Johan Varghese Kennady", "Electrical Engineering", "B.Tech.", "2024"])
    # Same name in two departments - forces the ambiguous-name path.
    sheet.append(["24B9001", "Rohit Sharma", "Mechanical Engineering", "B.Tech.", "2024"])
    sheet.append(["24B9002", "Rohit Sharma", "Civil Engineering", "B.Tech.", "2024"])
    wb.save(path)
    monkeypatch.setattr(roll_lookup, "LOOKUP_PATH", path)
    load_roll_department_maps.cache_clear()
    yield
    load_roll_department_maps.cache_clear()


def test_adds_department_column_for_recognized_roll_number():
    html = (
        "<table><thead><tr><th>Roll Number</th><th>Name</th></tr></thead>"
        "<tbody><tr><td>24B0945</td><td>Bhavesh Ramakrishnan Karthik</td></tr></tbody></table>"
    )
    result = annotate_roll_departments(html)
    assert "<th>Roll Number</th><th>Name</th><th>Department</th>" in result
    assert "<td>24B0945</td><td>Bhavesh Ramakrishnan Karthik</td><td>Computer Science and Engineering</td>" in result


def test_leaves_row_untouched_when_roll_number_not_in_lookup():
    html = "<table><tr><td>24B9999</td><td>Someone Unknown</td></tr></table>"
    result = annotate_roll_departments(html)
    assert result == html


def test_returns_html_unchanged_when_no_table_present():
    html = "<p>Deadline: 21st July 2026</p>"
    assert annotate_roll_departments(html) == html


def test_falls_back_to_name_match_when_roll_number_missing():
    html = "<table><tr><td>-</td><td>Bhavesh Ramakrishnan Karthik</td></tr></table>"
    result = annotate_roll_departments(html)
    assert "<td>Computer Science and Engineering</td>" in result


def test_ambiguous_name_uses_llm_to_pick_from_its_own_candidates():
    html = "<table><tr><td>-</td><td>Rohit Sharma</td></tr></table>"

    class FakeMessage:
        content = json.dumps({"Rohit Sharma": "Civil Engineering"})

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    result = annotate_roll_departments(html, attempts=[(FakeClient(), "fake-model")])
    assert "<td>Civil Engineering</td>" in result


def test_ambiguous_name_left_unresolved_without_llm_attempts():
    html = "<table><tr><td>-</td><td>Rohit Sharma</td></tr></table>"
    result = annotate_roll_departments(html)
    assert result == html
