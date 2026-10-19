"""Entry point for subscrub-core.exe, the engine the native Windows app (winui/) drives. See subscrub/helper.py."""
import sys

from subscrub.helper import main

if __name__ == "__main__":
    sys.exit(main())
