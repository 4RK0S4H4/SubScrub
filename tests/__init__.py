
# A test that hangs (a dialog nobody can click, a tool that never exits) should say where, not sit there for hours:
# set SUBSCRUB_TEST_TIMEOUT=<seconds> and the stack of every thread is printed before the run is stopped.
import os as _os

if _os.environ.get("SUBSCRUB_TEST_TIMEOUT"):
    import faulthandler as _fh

    _fh.dump_traceback_later(int(_os.environ["SUBSCRUB_TEST_TIMEOUT"]), exit=True)

# no test window should ask GitHub about updates
_os.environ.setdefault("SUBSCRUB_NO_UPDATE_CHECK", "1")
