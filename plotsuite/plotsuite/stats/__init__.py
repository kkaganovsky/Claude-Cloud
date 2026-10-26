from .core import (REGISTRY, BlockedError, Comparison, Issue, Option, Result, TestSpec, defaults, evaluate, fmt_p,  # noqa: F401
                   listing, run, stars)
from . import column  # noqa: F401  (registers tests)
from . import grouped  # noqa: F401
from . import xy  # noqa: F401
from . import survival, contingency  # noqa: F401
from . import misc  # noqa: F401
