"""Phase 9.0: frozen application's isolated HTTP status-poll contract.

Only synthetic in-memory responses; no SSH, production logs or cache.
"""
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from scripts.bench_phase9_frozen_real import wait_for


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class _SequenceOpener:
    def __init__(self, *sequence):
        self.sequence = list(sequence)
        self.calls = 0

    def open(self, *_args, **_kwargs):
        self.calls += 1
        outcome = self.sequence.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return _Response(json.dumps(outcome).encode("utf-8"))


class FrozenStatusTests(unittest.TestCase):
    def test_transient_poll_retries_during_build(self):
        opener = _SequenceOpener(
            urllib.error.URLError("synthetic connection reset"),
            {"busy": True, "error": ""},
            {"busy": False, "error": "", "result": {"reused": False}},
        )
        with patch("scripts.bench_phase9_frozen_real.sleep"):
            result = wait_for(opener, "http://127.0.0.1:1",
                              busy=False, seconds=3)
        self.assertEqual(opener.calls, 3)
        self.assertFalse(result["busy"])

    def test_transient_poll_retries_at_startup(self):
        opener = _SequenceOpener(
            urllib.error.URLError("synthetic startup delay"),
            {"busy": False, "error": ""},
        )
        with patch("scripts.bench_phase9_frozen_real.sleep"):
            self.assertFalse(wait_for(opener, "http://127.0.0.1:1",
                                      seconds=3)["busy"])
        self.assertEqual(opener.calls, 2)

    def test_http_rejection_is_not_retried(self):
        rejected = urllib.error.HTTPError(
            "http://127.0.0.1:1/api/status", 403,
            "synthetic rejection", {}, io.BytesIO(b"rejected"))
        opener = _SequenceOpener(rejected)
        with self.assertRaises(urllib.error.HTTPError):
            wait_for(opener, "http://127.0.0.1:1",
                     busy=False, seconds=3)
        self.assertEqual(opener.calls, 1)

    def test_worker_error_is_not_treated_as_success(self):
        opener = _SequenceOpener(
            {"busy": True, "error": ""},
            {"busy": False, "error": "synthetic failure"},
        )
        with patch("scripts.bench_phase9_frozen_real.sleep"):
            with self.assertRaisesRegex(AssertionError,
                                        "Frozen runtime reported failure"):
                wait_for(opener, "http://127.0.0.1:1",
                         busy=False, seconds=3)
        self.assertEqual(opener.calls, 2)


if __name__ == "__main__":
    unittest.main()
