"""Tests run on their own database, so a test run never resets the demo state in data/state.db."""
import os
import tempfile

os.environ.setdefault("CTD_DB", os.path.join(tempfile.mkdtemp(prefix="ctd-tests-"), "state.db"))
