from slow_log_parser import SlowQuery, parse_slow_log, wrap_single_sql


SAMPLE = """# Time: 2026-09-26T10:00:00.000000+08:00
# User@Host: root[root] @ localhost [127.0.0.1]  Id: 123
# Query_time: 1.234567  Lock_time: 0.000123  Rows_sent: 10  Rows_examined: 100000
SET timestamp=1727316000;
SELECT * FROM orders WHERE status='pending' ORDER BY create_time DESC LIMIT 10;
# Time: 2026-09-26T10:01:00.000000+08:00
# User@Host: root[root] @ localhost [127.0.0.1]  Id: 124
# Query_time: 2.000000  Lock_time: 0.000001  Rows_sent: 1  Rows_examined: 500000
SET timestamp=1727316060;
use test;
SELECT id FROM orders WHERE user_id = 12345;
"""


def test_parse_two_queries():
    qs = parse_slow_log(SAMPLE)
    assert len(qs) == 2
    assert qs[0].query_time == 1.234567
    assert qs[0].rows_sent == 10
    assert qs[0].rows_examined == 100000
    assert "SET timestamp" not in qs[0].sql
    assert "status='pending'" in qs[0].sql
    assert qs[1].query_time == 2.0
    assert qs[1].rows_examined == 500000


def test_wrap_single_sql():
    q = wrap_single_sql("SELECT * FROM t WHERE id=1;")
    assert q.sql == "SELECT * FROM t WHERE id=1"


def test_is_slow():
    assert SlowQuery(sql="x", query_time=2.0).is_slow
    assert SlowQuery(sql="x", rows_sent=10, rows_examined=1000).is_slow
    assert not SlowQuery(sql="x", rows_sent=10, rows_examined=20).is_slow
