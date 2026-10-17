"""Clean file names from TMDB.

    Batman V Super Dawn of Justice 2160 HDR DV DDP.mkv   ->  Batman v Superman - Dawn of Justice.mkv
    Green.Lantern.TAS.S01E09.1080p.x265-EDGE.mkv         ->  Green Lantern - The Animated Series - S01E09 - ...In Love and War.mkv

Three steps, each testable on its own:
  parse()  reads the release name: title, year, season/episodes;
  match()  asks TMDB and scores what comes back (title, year, and the video's real length vs. TMDB's runtime);
  render() builds a Windows-safe file name.
Only renaming: no folders are created or moved.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# ============================================================================ parsing

# words that mark the end of the title in a release name
_JUNK = r"""
    \d{3,4}p | 4k | uhd | 2160 | 1080 | 720 | 480 | hdr\d*\+? | hdr10plus | dv | dovi | dolby | vision | sdr | hlg |
    [xh]\.?26[45] | hevc | avc | av1 | xvid | divx | vc-?1 | mpeg-?2 | 10-?bit | 8-?bit | hi10p? |
    web(?:-?dl|-?rip)? | blu-?ray | bd(?:rip|25|50|66|100)? | br-?rip | remux | dvd(?:rip|r|5|9)? | hd-?tv | hdrip |
    pdtv | uhd-?bd | hddvd | cam | ts | telesync | screener | scr |
    ddp?(?:\d(?:\.\d)?)? | dd\+ | e-?ac-?3 | ac-?3 | aac\d*(?:\.\d)? | dts(?:-?hd|-?x|-?ma)? | truehd | atmos | flac |
    opus | mp3 | lpcm | pcm | \d\.\d | 2ch | 6ch | 8ch |
    proper | repack | rerip | internal | limited | dubbed | subbed | multi | dual | dual-?audio | multisub |
    amzn | nf | dsnp | hmax | atvp | pcok | pmtp | hulu | stan | crav |
    extended | unrated | uncut | remastered | imax | criterion | directors? | theatrical | hybrid | complete
"""
_JUNK_RE = re.compile(rf"^(?:{_JUNK})$", re.I | re.X)
# "S01E02", "Show_S01E02" (an underscore is a word character, so no \b), "S2024E01" (a year as the season)
_SXE = re.compile(r"(?<![A-Za-z0-9])S(\d{1,4})[ ._-]*E(\d{1,3})((?:[ ._-]*-?[ ._-]*E\d{1,3})*)", re.I)
_NXN = re.compile(r"(?<![\d])(\d{1,2})x(\d{2,3})(?![\d])", re.I)
_LONG = re.compile(r"(?<![A-Za-z0-9])(?:Season|Series)[ ._-]*(\d{1,4})[ ._,-]*Episode[ ._-]*(\d{1,3})", re.I)
_YEAR = re.compile(r"^\(?(19\d\d|20\d\d)\)?$")
_TV_HINT = re.compile(r"\b(?:season|series)\s*\d+\b", re.I)


@dataclass
class Parsed:
    title: str
    year: int | None = None
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    episode_title: str = ""          # what the file name says (used only if TMDB has nothing)

    @property
    def is_episode(self) -> bool:
        return self.season is not None and bool(self.episodes)


def _words(text: str) -> list[str]:
    text = re.sub(r"\[\s*((?:19|20)\d\d)\s*\]", r"(\1)", text)           # "Movie [2020]" is a year, not a tag
    text = re.sub(r"\[[^\]]*\]|\{[^}]*\}", " ", text)                  # [release group], {tags}
    text = re.sub(r"[._]+(?=[-–—][._\s])", " ", text)                    # Mission.Impossible.-.Fallout
    text = re.sub(r"(?<=[-–—])[._]+(?=\w)", " ", text)
    text = re.sub(r"(?<=[\w)\]])[._]+(?=[\w(\[])|(?<=\w)[._]+(?=[)\]])", " ", text)   # Green.Lantern.(2020).S01
    text = re.sub(r"_", " ", text)
    return [w for w in re.split(r"\s+", text) if w]


def _until_junk(words: list[str], allow_year: bool = True) -> tuple[list[str], int | None]:
    """Title words up to the first junk word, and the release year if one comes right after the title."""
    out, year = [], None
    this_year = time.localtime().tm_year
    for w in words:
        bare = w.strip("()[]{}-–—,.")
        if allow_year and out and _YEAR.match(w.strip(".,-")):
            y = int(_YEAR.match(w.strip(".,-")).group(1))
            if 1900 <= y <= this_year + 1:
                year = y                                 # keep going: "Blade Runner 2049 (2017)" -> 2017 wins
                out.append(w)
                continue
        if bare and _JUNK_RE.match(bare):
            break
        if re.fullmatch(r"\(.*\)", w) and any(_JUNK_RE.match(x) for x in re.split(r"[ ,]+", w.strip("()"))):
            break                                         # "(1080p x265 EDGE2020)"
        if w.startswith("(") and len(out) and any(_JUNK_RE.match(x.strip("()")) for x in [w]):
            break
        if re.fullmatch(r"-[A-Za-z0-9]+", w) and out:   # -GROUP at the end
            break
        out.append(w)
    if year is not None:
        # the year is the last year-like word; drop it (and anything after it) from the title
        for k in range(len(out) - 1, -1, -1):
            m = _YEAR.match(out[k].strip(".,-"))
            if m and int(m.group(1)) == year:
                out = out[:k]
                break
    return out, year


def _tidy(words: list[str]) -> str:
    t = " ".join(words)
    t = re.sub(r"\s*\(\s*\)\s*", " ", t)
    t = re.sub(r"\s+-\s*$|^\s*-\s+", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–—_.,")
    return t


def parse(filename: str) -> Parsed:
    stem = Path(filename).stem
    m = _SXE.search(stem) or _NXN.search(stem) or _LONG.search(stem)
    if m:
        season, first = int(m.group(1)), int(m.group(2))
        eps = [first]
        if m.re is _SXE and m.group(3):
            more = [int(x) for x in re.findall(r"E(\d{1,3})", m.group(3), re.I)]
            if more and re.search(r"-", m.group(3)) and len(more) == 1 and more[0] > first:
                eps = list(range(first, more[0] + 1))          # S01E01-E03
            else:
                eps += [e for e in more if e not in eps]
        before, after = stem[:m.start()], stem[m.end():]
        words, year = _until_junk(_words(before))
        title = _tidy(words)
        ep_words, _ = _until_junk(_words(after.lstrip(" ._-")), allow_year=False)
        return Parsed(title, year, season, eps, _tidy(ep_words))
    words, year = _until_junk(_words(stem))
    return Parsed(_tidy(words), year)


# ============================================================================ Windows-safe names

_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(text: str) -> str:
    """Make a title usable as a Windows file name, the way media managers usually do it:
    'Batman v Superman: Dawn of Justice' -> 'Batman v Superman - Dawn of Justice'."""
    t = unicodedata.normalize("NFC", text)
    t = re.sub(r"\s*:\s+", " - ", t)                    # "Title: Subtitle"
    t = t.replace(":", "-")                              # "10:30" style
    t = re.sub(r"\s*/\s*", "-", t) if "/" in t else t    # Face/Off -> Face-Off
    t = t.replace("\\", "-").replace("|", "-")
    t = t.replace('"', "'")
    t = re.sub(r"[<>?*]", "", t)
    t = re.sub(r"[\x00-\x1f]", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    t = t.rstrip(". ")                                   # Windows drops trailing dots and spaces
    if t.split(".")[0].upper() in _RESERVED:
        t += "_"
    return t or "Untitled"


@dataclass
class Choice:
    """One possible identity for a file."""
    kind: str                         # movie | episode
    tmdb_id: int
    title: str                        # movie title or show name
    year: int | None = None
    season: int | None = None
    episodes: list[int] = field(default_factory=list)
    episode_titles: list[str] = field(default_factory=list)
    runtime: int | None = None        # minutes, when TMDB knows it
    score: float = 0.0
    why: list[str] = field(default_factory=list)

    def label(self) -> str:
        if self.kind == "episode":
            ep = self.episode_code()
            return f"{self.title}, {ep}" + (f": {self.episode_titles[0]}" if self.episode_titles else "")
        return self.title + (f" ({self.year})" if self.year else "")

    def episode_code(self) -> str:
        """'S7 E2' (no zero padding); a multi-episode file reads 'S2 E1-E2'."""
        if not self.episodes:
            return ""
        code = f"S{self.season} E{self.episodes[0]}"
        if len(self.episodes) > 1:
            code += f"-E{self.episodes[-1]}"
        return code


def render(choice: Choice, ext: str, with_year: bool = False, max_len: int = 180) -> str:
    """The new file name (no folder). Movies: 'Title.mkv' or 'Title (Year).mkv'.
    Episodes: 'The Walking Dead S7 E2.mkv' (multi-episode files: 'Show S2 E1-E2.mkv')."""
    ext = ext if ext.startswith(".") else "." + ext
    if choice.kind == "movie":
        name = safe_name(choice.title)
        if with_year and choice.year:
            name += f" ({choice.year})"
        return name[:max_len].rstrip(". ") + ext
    code = choice.episode_code()
    show = safe_name(choice.title)[: max(10, max_len - len(code) - 1)].rstrip(". ")
    return f"{show} {code}".strip() + ext


# ============================================================================ TMDB

class LookupError_(Exception):
    """TMDB couldn't be reached or refused the key."""


class TMDB:
    BASE = "https://api.themoviedb.org/3"

    def __init__(self, key: str, timeout: float = 12.0, opener=None):
        self.key = (key or "").strip()
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen
        self._cache: dict[str, object] = {}
        self._lock = threading.Lock()

    def _get(self, path: str, **params):
        params = {k: v for k, v in params.items() if v not in (None, "")}
        params.setdefault("language", "en-US")
        headers = {"Accept": "application/json", "User-Agent": "SubScrub"}
        if len(self.key) > 40:                     # v4 "read access token"
            headers["Authorization"] = f"Bearer {self.key}"
        else:                                      # v3 API key
            params["api_key"] = self.key
        url = f"{self.BASE}{path}?{urllib.parse.urlencode(params)}"
        cache_key = path + "?" + urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items() if k != "api_key"))
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]
        req = urllib.request.Request(url, headers=headers)
        last = None
        for attempt in range(3):
            try:
                with self._open(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise LookupError_("TMDB didn't accept the API key (check it in Settings).") from e
                if e.code == 404:
                    data = None
                    break
                if e.code == 429:                 # rate limited: wait and retry
                    last = e
                    time.sleep(1.0 + attempt)
                    continue
                raise LookupError_(f"TMDB answered with an error ({e.code}).") from e
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = e
                time.sleep(0.5)
        else:
            raise LookupError_(f"Couldn't reach TMDB ({getattr(last, 'reason', last)}).")
        with self._lock:
            self._cache[cache_key] = data
        return data

    def search_movie(self, query: str, year: int | None = None) -> list[dict]:
        return (self._get("/search/movie", query=query, year=year, include_adult="false") or {}).get("results", [])

    def movie(self, tmdb_id: int) -> dict:
        return self._get(f"/movie/{tmdb_id}") or {}

    def search_tv(self, query: str, year: int | None = None) -> list[dict]:
        return (self._get("/search/tv", query=query, first_air_date_year=year) or {}).get("results", [])

    def season(self, tmdb_id: int, season: int) -> dict:
        return self._get(f"/tv/{tmdb_id}/season/{season}") or {}


# ============================================================================ matching

def _norm_tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = t.replace("&", " and ").replace("'", "")
    toks = re.findall(r"[a-z0-9]+", t)
    out = []
    for w in toks:
        if w == "vs":
            w = "v"
        if w in ("the", "a", "an") and out == []:
            continue                                     # leading article
        out.append(w)
    return out


def title_similarity(query: str, candidate: str) -> float:
    """0..1. Generous with truncated words ('Super' ~ 'Superman') and missing leading articles."""
    q, c = _norm_tokens(query), _norm_tokens(candidate)
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    hits = 0.0
    used = set()
    for w in q:
        best, bi = 0.0, None
        for i, x in enumerate(c):
            if i in used:
                continue
            if w == x:
                s = 1.0
            elif len(w) >= 3 and x.startswith(w):
                s = 0.85
            elif len(w) >= 4 and difflib.SequenceMatcher(None, w, x).ratio() >= 0.8:
                s = 0.7
            else:
                s = 0.0
            if s > best:
                best, bi = s, i
        if bi is not None:
            used.add(bi)
        hits += best
    precision = hits / len(q)
    recall = hits / len(c)
    seq = difflib.SequenceMatcher(None, " ".join(q), " ".join(c)).ratio()
    return round(0.45 * precision + 0.35 * recall + 0.2 * seq, 4)


def _year_of(date: str | None) -> int | None:
    m = re.match(r"(\d{4})", date or "")
    return int(m.group(1)) if m else None


def _queries(title: str) -> list[str]:
    """The title, then shorter versions of it (release names often add junk TMDB doesn't know)."""
    words = title.split()
    out = [title]
    for n in range(len(words) - 1, 1, -1):
        out.append(" ".join(words[:n]))
    if " - " in title:
        out.insert(1, title.replace(" - ", " "))
    return list(dict.fromkeys(q for q in out if q.strip()))


@dataclass
class Match:
    parsed: Parsed
    choices: list[Choice]
    confident: bool
    note: str = ""

    @property
    def best(self) -> Choice | None:
        return self.choices[0] if self.choices else None


HIGH = 0.82          # score a match needs to be applied without asking
MARGIN = 0.08        # ...and how far ahead of the runner-up it must be


def match(api: TMDB, filename: str, duration_sec: float = 0.0) -> Match:
    p = parse(filename)
    if not p.title:
        return Match(p, [], False, "No title could be read from the file name.")
    if p.is_episode:
        return _match_episode(api, p)
    return _match_movie(api, p, duration_sec)


def _match_movie(api: TMDB, p: Parsed, duration_sec: float) -> Match:
    seen: dict[int, dict] = {}
    for q in _queries(p.title)[:5]:
        for r in api.search_movie(q, p.year) + ([] if p.year is None else api.search_movie(q)):
            seen.setdefault(r["id"], r)
        good = [r for r in seen.values()
                if max(title_similarity(p.title, r.get("title", "")),
                       title_similarity(p.title, r.get("original_title", ""))) >= 0.75]
        if good:
            break
    cands = []
    for r in seen.values():
        sim = max(title_similarity(p.title, r.get("title", "")), title_similarity(p.title, r.get("original_title", "")))
        y = _year_of(r.get("release_date"))
        c = Choice("movie", r["id"], r.get("title") or r.get("original_title", ""), y, score=sim)
        c.why.append(f"title {sim:.0%}")
        if p.year and y:
            if y == p.year:
                c.score += 0.12
                c.why.append("year matches")
            elif abs(y - p.year) == 1:
                c.score += 0.04
            else:
                c.score -= 0.25
                c.why.append(f"year {y} vs {p.year}")
        c.score += min(0.03, (r.get("popularity") or 0) / 2000)         # tie-breaker only
        cands.append(c)
    cands.sort(key=lambda c: -c.score)
    # runtime: the strongest evidence there is, so check it for the leading few
    minutes = duration_sec / 60 if duration_sec else 0
    if minutes:
        for c in cands[:4]:
            info = api.movie(c.tmdb_id)
            c.runtime = info.get("runtime") or None
            if c.runtime:
                diff = abs(c.runtime - minutes)
                if diff <= 4:
                    c.score += 0.12
                    c.why.append("length matches")
                elif diff >= 15:
                    c.score -= 0.15
                    c.why.append(f"length {c.runtime} min vs {minutes:.0f}")
        cands.sort(key=lambda c: -c.score)
    cands = cands[:6]
    if not cands:
        return Match(p, [], False, f"TMDB has nothing called “{p.title}”.")
    lead = cands[0].score - (cands[1].score if len(cands) > 1 else 0)
    confident = cands[0].score >= HIGH and lead >= MARGIN
    return Match(p, cands, confident)


def _match_episode(api: TMDB, p: Parsed) -> Match:
    seen: dict[int, dict] = {}
    for q in _queries(p.title)[:5]:
        for r in api.search_tv(q):
            seen.setdefault(r["id"], r)
        if any(title_similarity(p.title, r.get("name", "")) >= 0.75 for r in seen.values()):
            break
    shows = []
    for r in seen.values():
        sim = max(title_similarity(p.title, r.get("name", "")), title_similarity(p.title, r.get("original_name", "")))
        y = _year_of(r.get("first_air_date"))
        score = sim + min(0.03, (r.get("popularity") or 0) / 2000)
        if p.year and y:
            score += 0.12 if y == p.year else (-0.2 if abs(y - p.year) > 1 else 0)
        shows.append((score, sim, r, y))
    shows.sort(key=lambda s: -s[0])
    cands = []
    for score, sim, r, y in shows[:4]:
        season = api.season(r["id"], p.season)
        eps = {e.get("episode_number"): e for e in season.get("episodes", [])}
        found = [eps[n] for n in p.episodes if n in eps]
        c = Choice("episode", r["id"], r.get("name", ""), y, p.season, list(p.episodes),
                   [e.get("name", "") for e in found], score=score)
        c.why.append(f"title {sim:.0%}")
        if len(found) == len(p.episodes):
            c.score += 0.1
            c.why.append(f"has {c.episode_code()}")
            rt = [e.get("runtime") for e in found if e.get("runtime")]
            c.runtime = sum(rt) if rt else None
        else:
            c.score -= 0.3
            c.why.append(f"no {c.episode_code()} on TMDB")
        cands.append(c)
    cands.sort(key=lambda c: -c.score)
    if not cands:
        return Match(p, [], False, f"TMDB has no show called “{p.title}”.")
    lead = cands[0].score - (cands[1].score if len(cands) > 1 else 0)
    confident = cands[0].score >= HIGH and lead >= MARGIN and bool(cands[0].episode_titles)
    return Match(p, cands, confident)


def search(api: TMDB, text: str, kind: str, parsed: Parsed) -> list[Choice]:
    """A title the user typed: list what TMDB has, keeping the season/episode read from the file name."""
    text = text.strip()
    if not text:
        return []
    p = Parsed(text, None, parsed.season, list(parsed.episodes))
    if kind == "episode" and p.is_episode:
        return _match_episode(api, p).choices
    return _match_movie(api, p, 0).choices


# ============================================================================ rename history (for undo)

def history_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    p = Path(base) / "SubScrub"
    p.mkdir(parents=True, exist_ok=True)
    return p / "renames.jsonl"


def record_rename(batch_id: str, old: str, new: str):
    try:
        with open(history_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps({"batch": batch_id, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "old": old, "new": new}) + "\n")
    except OSError:
        pass


def last_batch() -> list[dict]:
    try:
        rows = [json.loads(x) for x in history_path().read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []
    rows = [r for r in rows if not r.get("undone")]
    if not rows:
        return []
    bid = rows[-1]["batch"]
    return [r for r in rows if r["batch"] == bid]


def mark_undone(batch_id: str):
    try:
        p = history_path()
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        for r in rows:
            if r["batch"] == batch_id:
                r["undone"] = True
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    except (OSError, ValueError):
        pass
