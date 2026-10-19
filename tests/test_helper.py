"""The engine helper for the native Windows app (subscrub/helper.py): its JSON protocol, end to end, the way
winui/ drives it: a real subprocess talking JSON lines over stdin/stdout.

    python -m tests.test_helper

Checks the protocol (replies, errors, events), settings in a throwaway file, that the helper cleans the fixture
subtitles exactly like the cleaner itself (parity with tests/fixtures/), Review (tracks, rules, one-line updates),
the questions at save time and, with ffmpeg + mkvmerge, a real save of generated videos.
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from subscrub import media, subformats as sf  # noqa: E402
from subscrub.cleaner import CleanOptions, clean_doc  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="subscrub_helper_"))
FIX = ROOT / "tests" / "fixtures"
TIMEOUT = float(os.environ.get("SUBSCRUB_TEST_TIMEOUT", "120"))


class Helper:
    def __init__(self, settings: Path, demo: bool = True):
        env = dict(os.environ, XDG_DATA_HOME=str(TMP / "xdg"), LOCALAPPDATA=str(TMP / "appdata"))
        cmd = [sys.executable, "-m", "subscrub.helper", "--settings", str(settings)] + (["--demo-tmdb"] if demo else [])
        self.p = subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env)
        self.replies: dict = {}
        self.events: "queue.Queue" = queue.Queue()
        self.cv = threading.Condition()
        self.next_id = 0
        self.raw: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=lambda: [None for _ in self.p.stderr], daemon=True).start()

    def _read(self):
        for line in self.p.stdout:
            text = line.decode("utf-8")
            self.raw.append(text)
            msg = json.loads(text)                   # every line on stdout must be protocol JSON
            if "event" in msg:
                self.events.put(msg)
            else:
                with self.cv:
                    self.replies[msg["id"]] = msg
                    self.cv.notify_all()

    def call(self, cmd: str, ok: bool = True, **args):
        self.next_id += 1
        rid = self.next_id
        self.p.stdin.write((json.dumps({"id": rid, "cmd": cmd, "args": args}) + "\n").encode("utf-8"))
        self.p.stdin.flush()
        with self.cv:
            if not self.cv.wait_for(lambda: rid in self.replies, TIMEOUT):
                raise TimeoutError(cmd)
            msg = self.replies.pop(rid)
        assert msg["ok"] == ok, f"{cmd}: {msg}"
        return msg["result"] if ok else msg["error"]

    def event(self, name: str, timeout: float = TIMEOUT, cond=lambda d: True):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                msg = self.events.get(timeout=0.2)
            except queue.Empty:
                continue
            if msg["event"] == name and cond(msg["data"]):
                return msg["data"]
        raise TimeoutError(name)

    def close(self):
        try:
            self.call("shutdown")
        except Exception:  # noqa: BLE001
            pass
        self.p.wait(timeout=30)


def check(cond, what):
    if not cond:
        raise AssertionError(what)
    print("  ok:", what)


def test_protocol_and_settings():
    print("protocol and settings")
    settings = TMP / "settings.json"
    h = Helper(settings)
    ready = h.event("ready")
    check(ready["settings"]["video_mode"] == "replace" and ready["settings"]["make_default"], "defaults like the Qt app")
    hello = h.call("hello")
    check(hello["protocol"] == 1 and len(hello["rules"]) == 10, "hello lists the ten edit types")
    err = h.call("nonsense", ok=False)
    check("unknown command" in err, "unknown commands are refused, not crashed on")
    err = h.call("remove", ok=False)
    check(err, "missing arguments come back as an error")
    v = h.call("settings_set", values={"keep_sdh": True, "rules": {"dialogue_dashes": False}, "lyrics": "keep",
                                        "tmdb_key": "  abc  "})
    check(v["keep_sdh"] and not v["rules"]["dialogue_dashes"] and v["lyrics"] == "keep" and v["tmdb_key"] == "abc",
          "settings change and come back")
    h.close()
    stored = json.loads(settings.read_text(encoding="utf-8"))
    check(stored["keep_sdh"] == "true" and stored["tmdb_key"] == "abc", "settings are kept in the file given")
    check(json.loads(stored["clean_options_v2"])["dialogue_dashes"] is False, "default edits stored like the Qt app")
    h = Helper(settings)
    check(h.event("ready")["settings"]["keep_sdh"], "and read back next time")
    h.close()
    check(all(json.loads(x) for x in h.raw), "nothing but JSON on stdout")


def test_fixture_parity():
    """A subtitle file on its own, through the helper, cleans exactly like the cleaner does."""
    print("fixture parity")
    h = Helper(TMP / "parity.json")
    h.event("ready")
    work = TMP / "parity"
    work.mkdir()
    for name in ("sdh_en.srt", "sdh_es.ass", "plain_en.srt"):
        shutil.copy(FIX / name, work / name)
    added = h.call("add", paths=[str(work / "sdh_en.srt"), str(work / "sdh_es.ass")])
    check(added["added"] == 2, "two subtitle files added")
    files = h.call("files")
    check([r["kind"] for r in files["rows"]] == ["subtitle", "subtitle"], "both rows are subtitle files")
    for row in files["rows"]:
        path = Path(row["path"])
        doc = sf.load(str(path), "ass" if path.suffix == ".ass" else "srt")
        res = clean_doc(doc, CleanOptions())
        view = h.call("review", uid=row["uid"], show="all")
        check(len(view["rows"]) == len(doc.cues), f"{path.name}: every line in Everything")
        for c in view["rows"]:
            want = res.results[c["cue"]]
            from subscrub.cleaner import visible_text
            expect = None if want.text is None else visible_text(want.text if want.changed else doc.cues[c["cue"]].text,
                                                                  doc.fmt)
            assert c["after"] == expect, (path.name, c, want)
            assert "".join(s["text"] for s in c["before"]) == visible_text(doc.cues[c["cue"]].text, doc.fmt)
        check(True, f"{path.name}: the same result as the cleaner, line by line")
        check(row["count"] == sum(1 for i, cc in enumerate(doc.cues) if res.final(i) != cc.text),
              f"{path.name}: the row counts the same lines to clean")
    # one-line updates
    uid = files["rows"][0]["uid"]
    view = h.call("review", uid=uid, show="changes")
    cue = view["rows"][0]["cue"]
    upd = h.call("toggle_line", uid=uid, cue=cue)
    check(upd["row"]["override"] == "orig" and upd["row"]["after"] == "".join(s["text"] for s in upd["row"]["before"]),
          "Keep Original puts the line back")
    upd = h.call("toggle_line", uid=uid, cue=cue)
    check(upd["row"]["override"] in ("", "clean"), "and Use Cleaned takes it back")
    # per-file edits
    view = h.call("set_rules", uid=uid, rules={"brackets": False}, lyrics="italic")
    check(view["rules"]["custom"] and not next(r for r in view["rules"]["rows"] if r["key"] == "brackets")["on"],
          "a file can have its own edits")
    view = h.call("reset_rules", uid=uid)
    check(not view["rules"]["custom"], "and go back to the defaults")
    h.close()


def test_save_flow():
    tools = media.find_tools()
    if not (tools.ffmpeg and tools.mkvmerge):
        print("save flow: skipped (needs ffmpeg and mkvmerge)")
        return
    print("save flow (real videos)")
    from tests import make_samples
    mkv, _ = make_samples.pipeline_samples(tools, TMP / "media")
    h = Helper(TMP / "save.json")
    h.event("ready")
    h.call("settings_set", values={"rename_on": False})
    h.call("add", paths=[str(mkv)])
    check(h.call("wait_idle")["idle"], "the video is read")
    files = h.call("files")
    row = files["rows"][0]
    check(row["state"] == "ready" and row["count"] > 0 and files["buttons"]["primary_visible"],
          f"ready with {row['count']} lines to clean")
    view = h.call("review", uid=row["uid"])
    check(any(t["selected"] for t in view["tracks"]) and view["rows"], "Review shows the tracks and the changes")
    q = h.call("save", answers={})
    if "ask" in q and q["ask"]["key"] == "decision":
        check(len(q["ask"]["options"]) >= 3, "unsure lines: asked what to do")
        q = h.call("save", answers={"decision": "guess"})
    check(q.get("ask", {}).get("key") == "rewrite", "asked before rewriting in place")
    q = h.call("save", answers={"decision": "guess", "rewrite": "go"})
    check(q.get("started") == 1, "the save starts")
    done = h.event("save_done", timeout=TIMEOUT * 2)
    check(done["counts"]["done"] == 1 and not done["counts"]["failed"], f"saved: {done['status']}")
    out = done["outputs"][0]
    info = media.probe(tools, out)
    eng = [t for t in info.subs if media.lang_short(t.language) == "en"]
    check(any(t.default for t in eng), "the cleaned English track is the default")
    files = h.call("files")
    check(files["rows"][0]["state"] == "done" and not files["buttons"]["primary_visible"],
          "the row says saved and the button is gone")
    h.close()


def main():
    try:
        test_protocol_and_settings()
        test_fixture_parity()
        test_save_flow()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("all helper tests passed")


if __name__ == "__main__":
    main()
