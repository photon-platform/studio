"""Backwards-compatibility re-export shim for `photon_platform.studio.speak`."""

from ..speak import *  # noqa: F401,F403
from ..speak import main

if __name__ == "__main__":
    main()
