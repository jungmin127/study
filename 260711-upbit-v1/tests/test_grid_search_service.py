from backend.grid_search_service import (
    _parse_progress_line,
    _parse_result_json_line,
    _parse_total_combos_line,
)


def test_parse_progress_line_extracts_done_and_total():
    assert _parse_progress_line("    완료 1,005/20,700건 (4.9%)") == (1005, 20700)


def test_parse_progress_line_returns_none_for_unrelated_line():
    assert _parse_progress_line("[1] 캔들 조회: KRW-SOL minutes60 2026-06-05 ~ 2026-08-03") is None


def test_parse_total_combos_line_extracts_total():
    line = "[2] 매수 조건 138개 x 매도 조건 150개 = 총 20,700개 조합"
    assert _parse_total_combos_line(line) == 20700


def test_parse_total_combos_line_returns_none_for_unrelated_line():
    assert _parse_total_combos_line("완료 1,005/20,700건 (4.9%)") is None


def test_parse_result_json_line_extracts_payload():
    line = 'RESULT_JSON: {"total_combos": 20700, "elapsed_sec": 1617.9, "saved": []}'
    assert _parse_result_json_line(line) == {"total_combos": 20700, "elapsed_sec": 1617.9, "saved": []}


def test_parse_result_json_line_returns_none_for_unrelated_line():
    assert _parse_result_json_line("완료.") is None
