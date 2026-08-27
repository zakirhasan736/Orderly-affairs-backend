import inspect

from app.auth import routes


def test_report_passing_is_not_blocked_by_survivor_checklist():
    source = inspect.getsource(routes.nextkin_report_owner_deceased)
    assert "MIN_DEATH_SIGNAL_CHECKS" not in source
    assert "Before reporting a passing" not in source
    assert "survivor checklist items" not in source
