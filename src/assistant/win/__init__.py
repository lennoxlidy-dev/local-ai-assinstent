"""Windows integration helpers.

Every module in here must be importable on non-Windows systems too (it is
developed and unit-tested on Linux). Windows-only imports therefore happen
inside functions or behind ``IS_WINDOWS`` checks, and callers get a clean
``False``/``None`` result instead of an ImportError.
"""

import sys

IS_WINDOWS = sys.platform == "win32"
