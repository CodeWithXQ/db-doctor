from rule_engine import diagnose


def _sig(**kw):
    base = {
        "full_scan": False,
        "no_index_used": False,
        "no_available_index": False,
        "using_filesort": False,
        "using_temporary": False,
        "low_selectivity": False,
        "max_rows": 10,
        "worst_type": "ref",
    }
    base.update(kw)
    return base


def _hint(**kw):
    base = {
        "leading_wildcard": False,
        "column_in_function": False,
        "select_star": False,
        "deep_pagination": False,
        "limit_offset": 0,
    }
    base.update(kw)
    return base


def test_r6_leading_wildcard():
    d = diagnose(_sig(), _hint(leading_wildcard=True))
    assert d is not None and "R6" in d.matched_rules


def test_r7_column_in_function():
    d = diagnose(_sig(), _hint(column_in_function=True))
    assert d is not None and "R7" in d.matched_rules


def test_r8_deep_pagination():
    d = diagnose(_sig(), _hint(deep_pagination=True, limit_offset=900000))
    assert d is not None and "R8" in d.matched_rules


def test_r1_missing_index():
    d = diagnose(
        _sig(full_scan=True, no_available_index=True, max_rows=100000, worst_type="ALL"),
        _hint(),
    )
    assert d is not None and "R1" in d.matched_rules


def test_r2_index_not_used():
    d = diagnose(_sig(no_index_used=True), _hint())
    assert d is not None and "R2" in d.matched_rules


def test_r3_filesort():
    d = diagnose(_sig(using_filesort=True), _hint())
    assert d is not None and "R3" in d.matched_rules


def test_r4_temporary():
    d = diagnose(_sig(using_temporary=True), _hint())
    assert d is not None and "R4" in d.matched_rules


def test_r5_low_selectivity():
    d = diagnose(_sig(low_selectivity=True), _hint())
    assert d is not None and "R5" in d.matched_rules


def test_no_match():
    assert diagnose(_sig(), _hint()) is None
