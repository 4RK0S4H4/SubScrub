"""subscrub-core: SubScrub's engine for the native Windows app (winui/), driven over stdin/stdout.

    subscrub-core.exe [--settings <file.json>] [--demo-tmdb] [--log <file>]
    python -m subscrub.helper ...                          (the same, from source)

One JSON object per line, UTF-8, both ways.
  request   {"id": 7, "cmd": "add", "args": {"paths": ["D:\\\\TV"]}}
  reply     {"id": 7, "ok": true, "result": {...}}      or   {"id": 7, "ok": false, "error": "why"}
  event     {"event": "files", "data": {...}}            (no id: sent whenever something changes)

Events: "files" (the whole Files view: rows, summary, footer), "review_changed" ({"uid"}: the file open in Review
changed in the background, ask for it again), "save_done" (counts, the status line, a message to show),
"ready" (once, at start, the same as the hello reply).

Commands (see COMMANDS below): hello, settings_set, theme, tools_rescan, add, leftovers, leftovers_action, remove,
clear, files, locks, review, select_track, force_clean, set_rules, reset_rules, promote_rules, toggle_line,
name_choices, name_search, name_pick, save, cancel, open_folder, undo_renames_question, undo_renames, log, wait_idle,
shutdown. Commands run in order on one thread; title searches and the "files in use" check run on their own so the
window never waits on the network.

--settings keeps the settings in a JSON file instead of the registry (tests, screenshots: the user's real settings
and TMDB key are never touched). --demo-tmdb answers title lookups from a built-in stand-in (no network, no key).
"""
from __future__ import annotations

import io
import json
import os
import sys
import threading
import traceback

from . import core

PROTOCOL = 1


class Server:
    def __init__(self, out, settings: str | None = None, demo_tmdb: bool = False, log_file: str | None = None):
        self._out = out
        self._write_lock = threading.Lock()
        factory = None
        if demo_tmdb:
            from .demo_tmdb import FakeTMDB
            factory = FakeTMDB
        self.session = core.Session(core.Store(settings), self.event, factory, log_file)
        if demo_tmdb and not self.session.cfg.tmdb_key:
            self.session.cfg.tmdb_key = "stand-in"
        self.running = True

    # ------------------------------------------------------------------ output
    def send(self, obj: dict):
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str)
        with self._write_lock:
            self._out.write(line + "\n")
            self._out.flush()

    def event(self, name: str, data):
        self.send({"event": name, "data": data})

    # ------------------------------------------------------------------ input
    def handle_line(self, line: str):
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except ValueError:
            self.send({"id": None, "ok": False, "error": "not JSON"})
            return
        rid, cmd, args = msg.get("id"), msg.get("cmd", ""), msg.get("args") or {}
        fn = COMMANDS.get(cmd)
        if fn is None:
            self.send({"id": rid, "ok": False, "error": f"unknown command {cmd!r}"})
            return
        if cmd in ASYNC:                      # may wait on the network or Windows: answer from its own thread
            threading.Thread(target=self._run, args=(rid, cmd, fn, args), daemon=True, name=cmd).start()
        else:
            self._run(rid, cmd, fn, args)

    def _run(self, rid, cmd, fn, args):
        try:
            result = fn(self, **args)
        except Exception as e:  # noqa: BLE001 – the window shows it
            self.session.log(f"{cmd} failed: {e}\n{traceback.format_exc()}")
            self.send({"id": rid, "ok": False, "error": str(e) or e.__class__.__name__})
            return
        self.send({"id": rid, "ok": True, "result": result})

    def serve(self, stream):
        self.event("ready", self.session.hello())
        for line in stream:
            self.handle_line(line)
            if not self.running:
                break
        self.session.close()


def _shutdown(srv: Server):
    srv.running = False
    return {}


def _wait_idle(srv: Server, timeout: float = 600):
    return {"idle": srv.session.wait_idle(timeout)}


S = lambda srv: srv.session  # noqa: E731
COMMANDS = {
    "hello": lambda srv: dict(S(srv).hello(), protocol=PROTOCOL),
    "settings_set": lambda srv, values: S(srv).set_settings(values),
    "theme": lambda srv, mode: S(srv).set_theme(mode),
    "tools_rescan": lambda srv: S(srv).rescan_tools(),
    "add": lambda srv, paths: S(srv).add(list(paths)),
    "leftovers": lambda srv, paths: S(srv).leftovers(list(paths)),
    "leftovers_action": lambda srv, paths, action, which=None: S(srv).leftovers_action(list(paths), action, which),
    "remove": lambda srv, uid: S(srv).remove(uid),
    "clear": lambda srv: S(srv).clear(),
    "files": lambda srv: S(srv).files_view(),
    "locks": lambda srv: S(srv).check_locks(),
    "review": lambda srv, uid, show=None, first=False: S(srv).review(uid, show, first),
    "select_track": lambda srv, uid, index, show=None: S(srv).select_track(uid, index, show),
    "force_clean": lambda srv, uid, show=None: S(srv).force_clean(uid, show),
    "set_rules": lambda srv, uid, rules, lyrics="", show=None: S(srv).set_rules(uid, rules, lyrics, show),
    "reset_rules": lambda srv, uid, show=None: S(srv).reset_rules(uid, show),
    "promote_rules": lambda srv, uid, show=None: S(srv).promote_rules(uid, show),
    "toggle_line": lambda srv, uid, cue: S(srv).toggle_line(uid, cue),
    "name_choices": lambda srv, uid: S(srv).name_choices(uid),
    "name_search": lambda srv, uid, text: S(srv).name_search(uid, text),
    "name_pick": lambda srv, uid, pick: S(srv).name_pick(uid, pick),
    "save": lambda srv, only=None, answers=None: S(srv).save(only, answers),
    "cancel": lambda srv: S(srv).cancel(),
    "open_folder": lambda srv, path=None: S(srv).open_folder(path),
    "undo_renames_question": lambda srv: S(srv).undo_renames_question(),
    "undo_renames": lambda srv: S(srv).undo_renames(),
    "log": lambda srv: {"text": "\n".join(S(srv).log_lines)},
    "wait_idle": _wait_idle,
    "shutdown": _shutdown,
}
ASYNC = {"name_search", "locks", "save", "wait_idle", "leftovers", "leftovers_action"}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    def opt(name):
        if name in argv:
            i = argv.index(name)
            return argv[i + 1] if i + 1 < len(argv) else None
        return None
    # the protocol owns stdout: anything else that prints (a library, a stray print) goes to stderr instead
    out = io.TextIOWrapper(os.fdopen(os.dup(sys.stdout.fileno()), "wb"), encoding="utf-8", newline="\n")
    try:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())       # tools started later inherit stderr, not the pipe
    except OSError:
        pass
    sys.stdout = sys.stderr
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    srv = Server(out, settings=opt("--settings"), demo_tmdb="--demo-tmdb" in argv, log_file=opt("--log"))
    srv.serve(stdin)
    return 0


if __name__ == "__main__":
    sys.exit(main())
