"""
conftest.py
-----------
Ensures the repository root (where base.py, wrapper.py, engines.py, samples.py
live) is importable when pytest collects tests from the tests/ subdirectory.
"""

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
