"""Shared test support: small, explicit helpers instead of per-file copies.

* ``db``       run/event rows and queries
* ``repos``    throwaway repositories (including the calc bug used by most pipeline tests)
* ``pipeline`` drive the real state machine with a scripted model
* ``engine``   a fake OpenAI-compatible HTTP endpoint
"""

from tests.support.db import event_types, fetch_events, insert_run, run_row
from tests.support.engine import OK_BODY, completion, fake_server
from tests.support.pipeline import run_scripted
from tests.support.repos import (
    CALC_BUG,
    CALC_TEST,
    FIX,
    PLAN,
    TASK,
    TEST_CMD,
    TEST_REPO,
    WRONG,
    edit,
    make_calc_repo,
)

__all__ = [
    "CALC_BUG", "CALC_TEST", "FIX", "OK_BODY", "PLAN", "TASK", "TEST_CMD", "TEST_REPO", "WRONG",
    "completion", "edit", "event_types", "fake_server", "fetch_events", "insert_run", "make_calc_repo",
    "run_row", "run_scripted",
]
