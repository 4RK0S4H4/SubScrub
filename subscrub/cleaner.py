"""Strip hearing-impaired (SDH/CC) annotations from subtitle cues, keeping only dialogue.

What it handles (tuned on a real library of WEB-DL, Blu-ray and OCR'd tracks):
  * sound descriptions in [brackets] and (parentheses), including nested ones and
    bracket "speaker IDs" like [Grace] or [man on radio]
  * speaker labels: "JOHN:", "DR. SMITH:", 'WILLIAM "BILLY" MACON:', "McDOWELL:",
    "NAOMI:Take it easy." (no space), labels glued after a sentence ("Nice.HOLDEN: Shut up."),
    labels in the middle of a line ("something nice? -IAN: No."), ">>" caption markers,
    and mixed-case labels ("Harrington:") when a file uses them consistently
  * music cues (♪ tense music ♪, ♪♪, (MUSIC FADES)) and song lyrics (removed, kept, or
    kept in italics without the ♪ marks)
  * on-screen text notes: [sign reads: EXIT] -> EXIT
  * dialogue dashes ("- Hi." / "- Hey.")
  * damage in tracks that were OCR'd from Blu-ray images: ♪ read as j" / I, "I" read as "["
  * the debris left behind: empty lines, stray commas and colons, empty tags

Formatting tags (<i>, {\\an8}, ASS override blocks) are protected while cleaning and cues
nothing matched are left byte-for-byte untouched.

Every edit is graded: risk 0 = routine, 1 = worth a glance, 2 = judgement call. The app uses
the grades to decide whether a file can be saved without review.
"""
from __future__ import annotations

import bisect
import difflib
import html
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from .subformats import SubtitleDoc, to_seconds

# ------------------------------------------------------------------------ options


@dataclass
class CleanOptions:
    brackets: bool = True           # [gunshot], [Grace]
    parentheses: bool = True        # (sighs), (ON PHONE)
    speaker_labels: bool = True     # JOHN:  (and Name: when the file uses that style)
    music_cues: bool = True         # ♪ tense music ♪, ♪♪
    lyrics: bool = False            # True = remove sung lyrics entirely
    lyric_symbols: bool = True      # lyrics that are kept: drop the ♪ marks, show them in italics
    dialogue_dashes: bool = True    # "- Hi." -> "Hi."
    onscreen_text: bool = True      # [sign reads: EXIT] -> EXIT
    ocr_fixes: bool = True          # repair OCR damage (j" -> ♪, "[help" -> "I help")
    sound_words: bool = True        # bare sound words on their own line: "whoosh", "pew pew pew", "ka-boom"
    quote_italics: bool = True      # files that mark off-screen voices with 'single quotes' -> italics
    mixed_case_labels: bool = False  # remove every "Name:" even in files that don't use them consistently

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "CleanOptions":
        o = cls()
        for k, v in (d or {}).items():
            if hasattr(o, k):
                setattr(o, k, bool(v))
        return o

    def copy(self, **changes) -> "CleanOptions":
        d = self.to_dict()
        d.update(changes)
        return CleanOptions(**d)


# rule id (as found in CueResult.rules) -> what the UI calls it
RULE_NAMES = {
    "brackets": "Sounds in [brackets]",
    "parentheses": "Sounds in (parentheses)",
    "labels": "Speaker names",
    "music": "Music cues",
    "lyrics": "Lyrics removed",
    "lyric_style": "Lyrics restyled",
    "dashes": "Dialogue dashes",
    "onscreen": "On-screen text",
    "ocr": "OCR repairs",
    "sound_words": "Sound words",
    "quotes": "Quoted voices → italics",
}

# ------------------------------------------------------------------------ patterns

PH_BASE = 0xE000                      # private-use chars stand in for formatting tags
PH = "[-]"
TAG_RE_TEXT = re.compile(r"<[^<>\n]*>|\{\\[^{}\n]*\}")   # SRT / VTT (+ {\an8} in SRT)
TAG_RE_ASS = re.compile(r"\{[^{}]*\}")                   # every {...} block in ASS is invisible
ASS_DRAWING = re.compile(r"\{[^{}]*\\p[1-9]")

BRACKETS = re.compile(r"[\[［【][^\[\]［］【】]*[\]］】]")
PARENS = re.compile(r"[(（][^()（）]*[)）]")
MUSIC = "♪♫♬♩🎵🎶"
MUSIC_RUN = f"[{MUSIC}]+"
MUSIC_SEG = re.compile(f"{MUSIC_RUN}([^{MUSIC}]*?){MUSIC_RUN}")
HASH_LYRIC = re.compile(rf"^({PH}*\s*(?:-\s*)?{PH}*)#\s+(.*?)\s*#({PH}*)\s*$")

U = "A-ZÀ-ÖØ-ÞЀ-ЯΑ-Ω"                # upper-case letters
L = "a-zß-öø-ÿа-яα-ω"                 # lower-case letters
_LWORD = rf"[\"“]?(?:Mc|Mac)?[{U}0-9#][{U}0-9.'’\"“”\-#/]*"
LABEL_BODY = rf"{_LWORD}(?:\s+(?:&|and|AND|{_LWORD})){{0,4}}"
LABEL_UPPER = re.compile(
    rf"^(?P<pre>{PH}*\s*(?:-\s*)?{PH}*)[\"“]?(?P<label>{LABEL_BODY})\s*(?P<sep>[:;])"
    rf"(?P<post>\s+|$|(?={PH})|(?=[{U}\"“'‘¿¡]))\s*"
)
LABEL_MIDLINE = re.compile(          # "Nice.HOLDEN: Shut up."  "something nice? -IAN: No."
    rf"(?:(?<=[.!?…])|(?<=[.!?…][\"”])|(?<=[.!?…] )|(?<=[.!?…][\"”] ))[ \t]*-?[ \t]*(?P<label>{LABEL_BODY})[ \t]*:(?=\s|[{U}\"“'‘])[ \t]*"
)
MIXED_NAME = rf"[{U}][{L}'’]+(?:[ -](?:[{U}][{U}{L}'’]*|\d+|on|of|the|over)){{0,3}}"
LABEL_MIXED = re.compile(rf"^(?P<pre>{PH}*\s*(?:-\s*)?{PH}*)(?P<label>{MIXED_NAME})\s*:\s+")
MIXED_MIDLINE = re.compile(rf"(?<=[.!?…])[ \t]+-?[ \t]*(?P<label>{MIXED_NAME})[ \t]*:[ \t]+")
MIXED_CANDIDATE = re.compile(rf"(?:^|(?<=[.!?…]\s)|(?<=-))\s*-?\s*({MIXED_NAME})\s*:\s+\S")
LABEL_STOP = {"NO", "OK", "OKAY", "YES", "OH", "HEY", "PS", "P.S.", "NOTE", "AM", "PM", "A.M.", "P.M.", "Q", "A",
              "WARNING", "ATTENTION", "CAUTION", "ALERT", "BREAKING", "UPDATE", "RE", "FYI"}
MIXED_STOP = {"warning", "note", "attention", "caution", "size", "speed", "homework", "answer", "question",
              "location", "subject", "re", "ps", "step", "rule", "lesson", "chapter", "part", "day", "level",
              "yes", "no", "okay", "ok", "well", "so", "look", "listen", "remember", "but", "and", "or", "plus",
              "first", "second", "third", "next", "then", "now", "here", "there", "mission", "goal", "rule one",
              "number one rule", "travel tip", "translation", "meaning", "reason", "problem", "point", "fact",
              "news flash", "spoiler", "bottom line", "lesson one", "exhibit a", "dear diary", "the answer is",
              "attention all", "ladies", "folks", "guys", "dude", "ma'am"}
GENERIC_SPEAKERS = re.compile(r"^(?:man|woman|men|women|girl|boy|kid|child|all|both|crowd|everyone|group|"
                              r"announcer|narrator|reporter|anchor|voice|computer|guard|officer|doctor|nurse|"
                              r"soldier|pilot|driver|waiter|waitress|host|operator|dispatcher|teacher|student)"
                              r"(?:\s+\d+|\s+#\d+)?$", re.I)
CC_ARROWS = re.compile(rf"^({PH}*\s*)(?:>>+|&gt;&gt;)\s*")

DESC_WORDS = re.compile(
    r"\b(music|musical|instrumental|playing|plays|continues|fades|fading|swells|intensifies|builds|resumes|"
    r"concludes|stops|humming|hums|whistling|whistles|vocalizing|singing|sings|theme|jingle|chiming|tune|"
    r"melody|song|fanfare|score)\b",
    re.I,
)
MUSIC_PHRASE = re.compile(
    r"\b(music|song|melody|instrumental|tune|theme|fanfare|score|jingle)\s+(?:\w+\s+)?"
    r"(playing|plays|continues|fades|fading|ends|stops|swells|builds|intensifies|resumes|concludes|rises|falls|"
    r"up|out|in)\b|\b(playing|plays)\s*$",
    re.I,
)
ONSCREEN = re.compile(
    r"[\[(]\s*(?:on-?screen\s+)?(?:the\s+)?(?:sign|screen|text|note|card|caption|title|banner|graffiti|headline|"
    r"letter|message|label|plaque|poster|display|newspaper|monitor|phone|computer|subtitle|writing)s?"
    r"(?:\s+on\s+screen)?\s+(?:reads?|says?|shows?|displays?|reading)\s*:?\s*(?P<t>[^\[\]()]+?)\s*[\])]"
    r"|[\[(]\s*(?:on-?screen(?:\s+text)?|text\s+on\s+screen|reads|text)\s*:\s*(?P<t2>[^\[\]()]+?)\s*[\])]",
    re.I,
)
ANNOTATION_HINT = re.compile(
    r"\b\w+(?:ing|s|ed)\b|\b(softly|quietly|loudly|whisper\w*|in [A-Z]\w+|on (?:phone|tv|radio|screen|speaker)|"
    r"over (?:phone|radio|pa|intercom|speaker|comm)|tv|radio|music|indistinct|inaudible|laugh\w*|sigh\w*|"
    r"continu\w*|both|all|crowd|man|woman|voice)\b",
    re.I,
)
VOCAL_SOUNDS = re.compile(r"^(humming|hums|whistling|whistles|vocalizing|vocalizes|singing|sings|chanting|scatting|"
                          r"yodeling|beatboxing|crooning|warbling|harmonizing|strumming|drumming|playing|continues|"
                          r"fades|ends|stops)$", re.I)
PRONOUNS = re.compile(r"\b(?:I|I'm|I'll|I've|I'd|me|my|mine|you|you're|your|we|we're|us|our)\b", re.I)
REAL_I_END = re.compile(r"\b(?:\w+n['’]t|do|am|did|was|have|had|can|will|could|would|should|if|so|and|than|as|me|"
                        r"say|said|think|thought|guess|bet|swear|hope|know|mean|wish|not|where|who|what|"
                        r"how|why|when|shall|may|might|must|nor|neither|too)\s+I[.!?,…]*$", re.I)
ONOMATOPOEIA = {
    "boom", "kaboom", "ka-boom", "booom", "whoosh", "woosh", "swoosh", "fwoosh", "whoom", "vwoom", "vroom", "zoom",
    "thud", "thunk", "thump", "whump", "zoop", "pew", "zap", "zzap", "beep", "boop", "bleep", "bloop", "growl",
    "crash", "clang", "clank", "clink", "pow", "kapow", "ka-pow", "blam", "bam", "wham", "whack", "thwack", "smack",
    "bang", "click", "clack", "whirr", "whir", "whizz", "whiz", "swish", "smash", "crunch", "crack", "snap",
    "splash", "splat", "sploosh", "buzz", "bzzt", "kzzt", "zzt", "hiss", "sizzle", "fizz", "pop", "ping", "ding",
    "dong", "tick", "tock", "rumble", "rumbling", "screech", "shing", "schwing", "zing", "boing", "bonk", "honk",
    "toot", "vrrr", "brrr", "thwip", "thwip-thwip", "rat-ta", "rat-a-tat", "ratatat", "tat", "ka-chunk",
    "chunk", "clunk", "klunk", "creak", "squeak", "squelch", "slosh", "drip", "plop", "plink", "twang", "whoo",
    "vroosh", "shoom", "fwip", "zip", "zwip", "slap", "slam", "clap", "whap", "thwomp", "kerplunk", "kersplat", "kssh", "ksshh", "pssh", "psst", "whomp", "womp", "chomp", "crackle",
}
SOUND_LINE = re.compile(r"^[a-z][a-z'\-]*(?:[ ,]+[a-z][a-z'\-]*){0,7}!?$")
CONTRACTION_START = re.compile(r"^(?:cause|cos|coz|em|til|till|tis|twas|round|bout|kay|course|fore|n|nuff|sup|cept|"
                               r"bout|scuse|member|ello|allo|ow|uns|ere|nother|ve|re|ll|d|s|m|t|n')\b", re.I)
WORD = re.compile(r"\w")
DASH_LINE = re.compile(rf"^({PH}*)\s*-(?!-)\s*")


def _has_words(s: str) -> bool:
    return bool(WORD.search(s))


def _visible(s: str) -> str:
    return re.sub(PH, "", s)


def _tags_only(s: str) -> str:
    return re.sub(r"[^-]", "", s)


def _is_music_description(content: str) -> bool:
    """True for '♪ tense music playing ♪' style descriptions, False for sung lyrics."""
    c = _visible(content).strip().strip("[]()").strip()
    if not c:
        return True
    words = c.split()
    letters = [ch for ch in c if ch.isalpha()]
    upper = bool(letters) and all(ch.isupper() for ch in letters)
    if MUSIC_PHRASE.search(c) and len(words) <= 8:
        return True
    if (c[0].islower() or upper) and DESC_WORDS.search(c) and len(words) <= 8:
        return True
    # "♪ humming ♪", "♪ softly whistling ♪"
    return len(words) <= 3 and c[0].islower() and bool(VOCAL_SOUNDS.search(words[-1]))


def _looks_like_annotation(content: str) -> bool:
    """Parenthetical content that is clearly a sound/manner note (vs. dialogue in parentheses)."""
    c = _visible(content).strip()
    letters = [ch for ch in c if ch.isalpha()]
    if not letters:
        return True
    if all(ch.isupper() for ch in letters):
        return True
    if c[0] in MUSIC:
        return True                                   # (♪ the church: "Under the milky way")
    words = c.split()
    if len(words) <= 4 and not re.search(r"[.?!]", c) and not PRONOUNS.search(c):
        return True                                   # (Frank), (Car horn), (Thunder, birdsong)
    return bool(ANNOTATION_HINT.search(c)) and len(words) <= 6 and not PRONOUNS.search(c)


def _label_ok(label: str) -> bool:
    lab = label.strip().strip("\"“”")
    if sum(ch.isalpha() for ch in lab) < 2 or lab.upper() in LABEL_STOP:
        return False
    if re.fullmatch(r"[\d\s.:]+", lab) or " - " in lab or len(lab.split()) > 5:
        return False
    return sum(1 for ch in lab if ch.isupper()) >= 2


# ------------------------------------------------------------------------ results & context


@dataclass
class CueResult:
    text: str | None        # None = cue removed
    changed: bool
    rules: set = field(default_factory=set)
    risk: int = 0           # 0 routine, 1 glance, 2 judgement call
    why: list = field(default_factory=list)


@dataclass
class CueCtx:
    """What the rest of the document says about one cue."""
    continues_next: bool | None = None       # an unclosed [ or ( here is closed in the next cue (None = unknown)
    continued_from_prev: bool | None = None  # a stray ] or ) here closes the previous cue's
    mixed_names: dict | None = None          # lower-case Name -> count, when the file uses "Name:" labels
    ocr_notes: str | None = None             # 'j' (♪ read as j") or 'I' (♪ read as I)
    ocr_suspect: bool = False
    quote_voices: bool = False               # the file wraps off-screen/radio voices in 'single quotes'


class _Rec:
    def __init__(self):
        self.rules: set[str] = set()
        self.risk = 0
        self.why: list[str] = []

    def hit(self, rule: str, risk: int = 0, why: str | None = None):
        self.rules.add(rule)
        self.risk = max(self.risk, risk)
        if why and why not in self.why:
            self.why.append(why)


def _protect(text: str, tag_re: re.Pattern) -> tuple[str, list[str]]:
    tags: list[str] = []

    def repl(m):
        tags.append(m.group(0))
        return chr(PH_BASE + len(tags) - 1)

    return tag_re.sub(repl, text), tags


def _restore(text: str, tags: list[str]) -> str:
    return re.sub(PH, lambda m: tags[ord(m.group(0)) - PH_BASE], text)


def _sub_loop(pattern: re.Pattern, text: str, repl=" ") -> tuple[str, bool]:
    hit = False
    while True:
        new = pattern.sub(repl, text)
        if new == text:
            return text, hit
        hit = True
        text = new


def _unclosed(vis: str, o: str, c: str) -> bool:
    depth = 0
    for ch in vis:
        if ch == o:
            depth += 1
        elif ch == c and depth:
            depth -= 1
    return depth > 0


def _stray_close(vis: str, o: str, c: str) -> bool:
    fc, fo = vis.find(c), vis.find(o)
    return fc != -1 and (fo == -1 or fc < fo)


# ------------------------------------------------------------------------ the cleaner


def clean_cue(text: str, opts: CleanOptions, fmt: str = "srt", ctx: CueCtx | None = None) -> CueResult:
    ctx = ctx or CueCtx()
    if fmt == "ass" and ASS_DRAWING.search(text):
        return CueResult(text, False)          # vector drawing / typesetting – leave it
    tag_re = TAG_RE_ASS if fmt == "ass" else TAG_RE_TEXT
    work, tags = _protect(text, tag_re)
    if len(tags) > 6000 or re.search(PH, text):
        return CueResult(text, False)
    rec = _Rec()
    already_italic = bool(re.search(r"<i>|\\i1", "".join(tags), re.I))
    ital_on, ital_off = ("{\\i1}", "{\\i0}") if fmt == "ass" else ("<i>", "</i>")

    def italic(t: str) -> str:
        t = t.strip()
        return t if (already_italic or not _has_words(_visible(t))) else f"{ital_on}{t}{ital_off}"

    # --- OCR damage (only where the document showed the pattern)
    if opts.ocr_fixes:
        if ctx.ocr_notes == "j":
            new = "\n".join(re.sub(r'\s*j\s?"\s*$', " ♪", re.sub(rf'^({PH}*\s*-?\s*{PH}*)j\s?"\s*', r"\1♪ ", l))
                            for l in work.split("\n"))
            new = re.sub(r"\(J'\s*", "(♪ ", new)
            if new != work:
                work = new
                rec.hit("ocr", 1, "♪ had been misread as j\" by OCR")
        elif ctx.ocr_notes == "I":
            ls = work.split("\n")
            if re.match(rf"^{PH}*\s*-?\s*{PH}*I\s", ls[0]) and re.search(rf"\sI{PH}*$", ls[-1]) and \
                    not REAL_I_END.search(_visible(ls[-1])):
                ls[0] = re.sub(rf"^({PH}*\s*-?\s*{PH}*)I\s", r"\1♪ ", ls[0], count=1)
                ls[-1] = re.sub(rf"\sI({PH}*)$", r" ♪\1", ls[-1])
                work = "\n".join(ls)
                rec.hit("ocr", 1, "♪ had been misread as I by OCR")
        if fmt != "ass" and re.search(r"\{(?=[A-Za-z])", work):
            work = re.sub(r"\{(?=[A-Za-z])", "[", work)
            rec.hit("ocr", 1, "[ had been misread as { by OCR")

    orig_lines = work.split("\n")

    # --- "# lyric #" style (old subtitle files) -> ♪ lyric ♪
    work = "\n".join(HASH_LYRIC.sub(r"\1♪ \2 ♪\3", l) for l in work.split("\n"))

    # --- on-screen text: [sign reads: EXIT] -> EXIT
    if opts.onscreen_text:
        def onscreen(m):
            t = (m.group("t") or m.group("t2") or "").strip().strip("\"“”'‘’").strip()
            rec.hit("onscreen", 1, "an on-screen text note was turned into the text itself")
            return t
        work = ONSCREEN.sub(onscreen, work)

    # --- sound descriptions & bracket speaker IDs
    if opts.brackets:
        work, hit = _sub_loop(BRACKETS, work)
        if hit:
            rec.hit("brackets")
        lines = []
        for l in work.split("\n"):
            v = _visible(l)
            m = re.search(r"[\[［【]([^\]］】]*)$", l)
            if m and _unclosed(v, m.group(0)[0], {"[": "]", "［": "］", "【": "】"}[m.group(0)[0]]):
                follow = _visible(m.group(1)).strip()
                before = l[:m.start()]
                if ctx.continues_next is not False:            # closes in the next cue (or we can't tell)
                    l = before + " "
                    rec.hit("brackets")
                elif (opts.ocr_fixes and ctx.ocr_suspect and re.match(rf"[{L}]+\b", follow)
                      and (not _visible(before).strip() or before[-1:] == " ")):
                    l = before + "I " + m.group(1).lstrip()
                    rec.hit("ocr", 2, "a “[” looked like an OCR'd “I” and was turned back into one")
                else:
                    l = before + " "
                    small = len(follow.split()) <= 1
                    rec.hit("brackets", 1 if small else 2,
                            "removed text after a “[” that never closes" + ("" if small else " — check it"))
            elif re.search(r"[\]］】]", v) and _stray_close(v, "[", "]"):
                l2 = re.sub(r"^[^\[［【]*?[\]］】]", " ", l)
                if l2 != l:
                    gone = _visible(l[:len(l) - len(l2) + 1])
                    risky = ctx.continued_from_prev is False and not _looks_like_annotation(gone.strip("] "))
                    l = l2
                    rec.hit("brackets", 2 if risky else 0, "removed text before a stray “]”" if risky else None)
            lines.append(l)
        work = "\n".join(lines)

    # --- a second speaker's name in the middle of a line ("Nice.HOLDEN: Shut up.", "nice? -IAN: No.")
    if opts.speaker_labels:
        def mid_repl(m) -> str:
            lead = m.group(0)[:m.start("label") - m.start()]
            if not lead:                                  # glued straight on: a line break got lost
                return "\n"
            return " -" if ("-" in lead and not opts.dialogue_dashes) else " "

        def midline(m):
            before = work[:m.start()]
            line_before = _visible(before.split("\n")[-1])
            if not _label_ok(m.group("label")) or not line_before.strip() or \
                    not re.search(f"[{L}]", line_before):           # "DR. SMITH:" is one label
                return m.group(0)
            rec.hit("labels", 1, "removed a second speaker's name from the middle of a line")
            return mid_repl(m)
        work = LABEL_MIDLINE.sub(midline, work)
        if ctx.mixed_names:
            def mmid(m):
                name = m.group("label").lower()
                if name in ctx.mixed_names or GENERIC_SPEAKERS.match(name):
                    rec.hit("labels", 1, "removed a second speaker's name from the middle of a line")
                    return mid_repl(m)
                return m.group(0)
            work = MIXED_MIDLINE.sub(mmid, work)

    if opts.parentheses:
        risky = []

        def paren(m):
            inner = m.group(0)[1:-1]
            if not _looks_like_annotation(inner):
                risky.append(inner)
            return " "
        work, hit = _sub_loop(PARENS, work, paren)
        if hit:
            rec.hit("parentheses", 2 if risky else 0,
                    f"removed “({risky[0].strip()})” — it could be dialogue" if risky else None)
        # an unclosed "(" whose note runs over several lines: (MUSIC FADING UP,\n"BRANDY
        m = re.search(r"[(（](?=[^()（）]*$)", work)
        if m and "\n" in work[m.start():] and _unclosed(_visible(work), "(", ")"):
            follow = _visible(work[m.start() + 1:])
            letters = [ch for ch in follow if ch.isalpha()]
            if letters and all(ch.isupper() for ch in letters):
                work = work[:m.start()] + " " + _tags_only(work[m.start():])
                rec.hit("parentheses", 0 if ctx.continues_next is not False else 1,
                        None if ctx.continues_next is not False else "a “(” never closes")
        lines = []
        for l in work.split("\n"):
            v = _visible(l)
            if _unclosed(v, "(", ")"):
                m = re.search(r"[(（][^)）]*$", l)
                if m and (ctx.continues_next is not False or _looks_like_annotation(m.group(0)[1:])):
                    l = l[:m.start()] + " "
                    rec.hit("parentheses", 0 if ctx.continues_next is not False else 1,
                            None if ctx.continues_next is not False else "a “(” never closes")
            elif _stray_close(v, "(", ")") and ctx.continued_from_prev is not False:
                l2 = re.sub(r"^[^(（]*?[)）]", " ", l)
                if l2 != l and _looks_like_annotation(_visible(l[:len(l) - len(l2)])):
                    l = l2
                    rec.hit("parentheses")
            lines.append(l)
        work = "\n".join(lines)

    # --- music
    restyle = (not opts.lyrics) and opts.lyric_symbols
    if opts.music_cues or opts.lyrics or restyle:
        def seg(m):
            is_desc = _is_music_description(m.group(1))
            if (is_desc and opts.music_cues) or (not is_desc and opts.lyrics):
                rec.hit("music" if is_desc else "lyrics")
                return " "
            if restyle and not is_desc:
                rec.hit("lyric_style")
                return italic(m.group(1))
            return m.group(0)

        work = MUSIC_SEG.sub(seg, work)
        lines, in_lyric = [], False
        for l in work.split("\n"):
            v = _visible(l).strip().lstrip("-").strip()
            has_note = any(ch in v for ch in MUSIC)
            starts = bool(v) and v[0] in MUSIC
            ends = bool(v) and v[-1] in MUSIC
            if has_note and not _has_words(v):                       # "♪♪", "♪"
                if opts.music_cues:
                    rec.hit("music")
                    lines.append(_tags_only(l))
                else:
                    lines.append(l)
                continue
            if in_lyric and not has_note and _visible(l).lstrip().startswith("-"):
                in_lyric = False                                      # "- OK, take off your clothes." – a new speaker
            if (in_lyric or starts or ends) and _has_words(v):
                if starts and not ends:
                    in_lyric = True
                elif ends:
                    in_lyric = False
                desc = _is_music_description(re.sub(f"[{MUSIC}]", "", v))
                if (desc and opts.music_cues) or (not desc and opts.lyrics):
                    rec.hit("music" if desc else "lyrics")
                    lines.append(_tags_only(l))
                    continue
                if restyle and not desc:
                    rec.hit("lyric_style")
                    dm = DASH_LINE.match(l)
                    prefix = dm.group(0) if dm else ""
                    lines.append(prefix + italic(re.sub(f"[{MUSIC}]+", "", l[len(prefix):])))
                    continue
            lines.append(l)
        work = "\n".join(lines)

    # --- speaker labels at the start of a line
    if opts.speaker_labels or opts.mixed_case_labels:
        ls = work.split("\n")
        lines = []
        for idx, l in enumerate(ls):
            if opts.speaker_labels:
                l2 = CC_ARROWS.sub(r"\1", l)
                if l2 != l:
                    rec.hit("labels")
                    l = l2
                m = LABEL_UPPER.match(l)
                if m:
                    rest = l[m.end():]
                    label = m.group("label")
                    ok = _label_ok(label)
                    if ok and m.group("sep") == ";" and (_visible(rest).strip() or idx == len(ls) - 1):
                        ok = False                          # "COMPUTER;" only when alone on its line
                    tail = rest if _visible(rest).strip() else "\n".join(ls[idx + 1:])
                    if ok and len(label.split()) >= 2 and _has_words(_visible(tail)) and \
                            not re.search(f"[{L}]", _visible(tail)):
                        ok = False                          # "PASSIVE SCAN: MAXIMUM RANGE" – on-screen text
                    if ok:
                        l = m.group("pre") + rest
                        semi = m.group("sep") == ";"
                        rec.hit("labels", 1 if semi else 0, "removed a speaker name written with “;”" if semi else None)
            m = LABEL_MIXED.match(l)
            if m:
                name = m.group("label").lower()
                known = ctx.mixed_names or {}
                if opts.speaker_labels and (name in known or (known and GENERIC_SPEAKERS.match(name))):
                    l = m.group("pre") + l[m.end():]
                    once = known.get(name, 0) < 2 and not GENERIC_SPEAKERS.match(name)
                    rec.hit("labels", 1 if once else 0,
                            f"removed “{m.group('label')}:” (only used once in this file)" if once else None)
                elif opts.mixed_case_labels and name not in MIXED_STOP:
                    l = m.group("pre") + l[m.end():]
                    rec.hit("labels", 2, f"removed “{m.group('label')}:” — it could be dialogue")
            lines.append(l)
        work = "\n".join(lines)

    # --- bare sound words: a line that is nothing but "whoosh" / "pew pew pew" / "ka-boom"
    if opts.sound_words and not (fmt == "ass" and re.search(r"\\(?:pos|move|k[fo]?\d|t\()", "".join(tags))):
        lines = []
        for l in work.split("\n"):
            v = _visible(l).strip().lstrip("-").strip()
            if v and SOUND_LINE.match(v) and all(w.strip(",!") in ONOMATOPOEIA for w in v.replace(",", " ").split()):
                rec.hit("sound_words")
                lines.append(_tags_only(l))
                continue
            lines.append(l)
        work = "\n".join(lines)

    # --- 'quoted' off-screen voices (files that use quotes instead of italics) -> italics
    if opts.quote_italics and ctx.quote_voices:
        ls = work.split("\n")
        open_at = close_at = None
        for i, l in enumerate(ls):
            v = _visible(l).strip()
            if not v:
                continue
            m = re.match(rf"^({PH}*\s*-?\s*{PH}*)['‘](?=\S)", l)
            if open_at is None and m and not CONTRACTION_START.match(_visible(l[m.end():])):
                open_at = i
                ls[i] = m.group(1) + l[m.end():]
            if re.search(rf"[^\s'’] ?['’]+({PH}*)\s*$", ls[i]) and not re.search(rf"\b\w+in['’]{PH}*\s*$", ls[i]):
                close_at = i
                ls[i] = re.sub(rf" ?['’]+({PH}*)\s*$", r"\1", ls[i])
        if open_at is not None or close_at is not None:
            first = open_at if open_at is not None else 0
            last = close_at if close_at is not None else len(ls) - 1
            if first <= last:
                for i in range(first, last + 1):
                    dm = DASH_LINE.match(ls[i])
                    prefix = dm.group(0) if dm else re.match(rf"^{PH}*", ls[i]).group(0)
                    ls[i] = prefix + italic(ls[i][len(prefix):])
                rec.hit("quotes")
            work = "\n".join(ls)

    # --- dialogue dashes
    if opts.dialogue_dashes:
        work, n = re.subn(rf"(?<=[.!?…,])({PH}*)[ \t]+-(?!-)[ \t]*(?=[\uE000-\uF8FF{U}\"“'‘¿¡.])", r"\1 ", work)  # "do. -Oh, God."
        if n:
            rec.hit("dashes")
        ls = work.split("\n")
        new = [DASH_LINE.sub(r"\1", l, count=1) if _visible(l).lstrip().startswith("-") else l for l in ls]
        if any(a != b and _has_words(_visible(b)) for a, b in zip(ls, new)):
            rec.hit("dashes")
        work = "\n".join(new)

    if not rec.rules:
        return CueResult(text, False)

    # --- tidy up what's left
    kept: list[str] = []
    carry = ""                                   # tags from dropped lines, e.g. an opening <i>
    for l in work.split("\n"):
        l = carry + l
        carry = ""
        l = re.sub(r"[ \t]{2,}", " ", l)
        l = re.sub(r"(?<!\.)\s+([,!?;:…]|\.(?!\.))", r"\1", l)
        l = re.sub(r"([,;])\s*[,;]+", r"\1", l)
        l = re.sub(rf"^({PH}*\s*(?:-\s*)?{PH}*)\s*[,;:]+\s*", r"\1", l)   # leading ", " left by removal
        if opts.dialogue_dashes:
            l = re.sub(rf"^((?:{PH}|\s)*?)\s+(?=\S)", r"\1", l)             # "<i> It" -> "<i>It"
        else:
            l = re.sub(rf"^({PH}*)\s+", r"\1", l)
            dm = re.match(rf"^{PH}*-(?!-)\s?", l)
            if dm:
                l = dm.group(0) + re.sub(rf"^({PH}*)\s+", r"\1", l[dm.end():])
        l = re.sub(rf"({PH}*)\s+({PH}*)$", r"\1\2", l).rstrip()
        if not _has_words(_visible(l)):
            carry = _tags_only(l)
            continue
        kept.append(l)
    if carry and kept:
        kept[-1] += carry

    if not kept:
        return CueResult(None, True, rec.rules, rec.risk, rec.why)

    # A two-speaker cue that lost one speaker: drop the now-pointless dash (when dashes are kept)
    if not opts.dialogue_dashes:
        orig_dashes = sum(1 for l in orig_lines if _visible(l).lstrip().startswith("-"))
        dash_idx = [i for i, l in enumerate(kept) if _visible(l).lstrip().startswith("-")]
        if orig_dashes >= 2 and len(dash_idx) == 1 and dash_idx[0] == 0:
            kept[0] = DASH_LINE.sub(r"\1", kept[0], count=1)

    out = _restore("\n".join(kept), tags)
    out = out.replace(f"{ital_off}\n{ital_on}", "\n")         # lyric lines italicised one by one -> one block
    if fmt != "ass":
        out, _ = _sub_loop(re.compile(r"<(\w+)[^<>]*>(\s*)</\1>"), out, r"\2")
        out = out.strip()
    if not _has_words(visible_text(out, fmt)):
        return CueResult(None, True, rec.rules, rec.risk, rec.why)
    if out == text:
        return CueResult(text, False)
    return CueResult(out, True, rec.rules, rec.risk, rec.why)


# ------------------------------------------------------------------------ document analysis


@dataclass
class DocNotes:
    mixed_names: dict = field(default_factory=dict)
    quote_voices: bool = False
    ocr_notes: str | None = None
    ocr_suspect: bool = False
    notes: list = field(default_factory=list)       # human-readable observations about the file


def analyse_doc(doc: SubtitleDoc) -> tuple[DocNotes, list[CueCtx]]:
    vis = [visible_text(c.text, doc.fmt) for c in doc.cues]
    dn = DocNotes()

    # "Name:" style labels used consistently through the file
    names = Counter()
    for v in vis:
        for line in v.split("\n"):
            for m in MIXED_CANDIDATE.finditer(line):
                n = m.group(1).strip()
                if n.lower() not in MIXED_STOP:
                    names[n.lower()] += 1
    repeated = [n for n, k in names.items() if k >= 2]
    if sum(names.values()) >= 8 and len(repeated) >= 3:
        total = sum(names.values())
        dn.mixed_names = {n: k for n, k in names.items()
                          if k >= 2 or GENERIC_SPEAKERS.match(n) or " " not in n}
        dn.notes.append("This file writes speaker names like “Peter:” — those are removed too.")

    # OCR'd tracks: ♪ read as j" / (J' or as a leading + trailing I
    j = sum(1 for v in vis for l in v.split("\n")
            if re.match(r'^\s*-?\s*j\s?"', l) or re.search(r'j\s?"\s*$', l) or "(J'" in l)
    inote = sum(1 for v in vis if re.match(r"^\s*-?\s*I\s", v) and re.search(r"\sI$", v) and not REAL_I_END.search(v))
    if j >= 3:
        dn.ocr_notes = "j"
    elif inote >= 3:
        dn.ocr_notes = "I"
    weird = sum(1 for v in vis if re.search(r"\[M1\b|\{[a-z]|\[[A-Z][a-z]+fl\b", v))
    lone = sum(1 for v in vis if _unclosed(v, "[", "]"))
    dn.ocr_suspect = bool(dn.ocr_notes) or weird >= 2 or lone >= 4
    if dn.ocr_suspect:
        dn.notes.append("This track looks machine-converted from disc images (OCR), so some lines may be garbled.")

    # 'Quoted' voices: off-screen/radio/narration wrapped in single quotes instead of italics
    def opens(v):
        m = re.match(r"^\s*(?:\([^)]*\)\s*)?-?\s*['‘](?=\w)", v)
        return bool(m) and not CONTRACTION_START.match(v[m.end():])

    def closes(v):
        return bool(re.search(r"[^\s'’] ?['’]\s*$", v)) and not re.search(r"\b\w+in['’]\s*$", v)
    wrapped = sum(1 for v in vis if opens(v) and closes(v))
    if wrapped >= 5 and wrapped >= 0.02 * len(vis):
        dn.quote_voices = True
        dn.notes.append("This file marks off-screen voices with 'quotes' — they're shown in italics instead.")

    ctxs = [CueCtx(mixed_names=dn.mixed_names or None, ocr_notes=dn.ocr_notes, ocr_suspect=dn.ocr_suspect,
                   quote_voices=dn.quote_voices) for _ in vis]
    for i, v in enumerate(vis):
        nxt = vis[i + 1] if i + 1 < len(vis) else ""
        for o, c in (("[", "]"), ("(", ")")):
            if _unclosed(v, o, c):
                closes = _stray_close(nxt, o, c)
                ctxs[i].continues_next = bool(closes) or bool(ctxs[i].continues_next)
                if closes:
                    ctxs[i + 1].continued_from_prev = True
    for i, v in enumerate(vis):
        if ctxs[i].continued_from_prev is None and (_stray_close(v, "[", "]") or _stray_close(v, "(", ")")):
            ctxs[i].continued_from_prev = False
    return dn, ctxs


# ------------------------------------------------------------------------ documents


@dataclass
class CleanStats:
    total: int = 0
    removed: int = 0
    edited: int = 0
    by_rule: dict = field(default_factory=dict)
    risky: int = 0          # cues with a judgement call (risk 2)
    glance: int = 0         # cues worth a glance (risk 1)

    @property
    def changed(self) -> int:
        return self.removed + self.edited


@dataclass
class CleanResult:
    doc: SubtitleDoc
    results: list[CueResult]
    stats: CleanStats
    notes: DocNotes = field(default_factory=DocNotes)
    overrides: dict = field(default_factory=dict)    # cue index -> "orig" | "clean" (user decisions)

    def final(self, i: int) -> str | None:
        return self.doc.cues[i].text if self.overrides.get(i) == "orig" else self.results[i].text

    def texts(self) -> list[str | None]:
        return [self.final(i) for i in range(len(self.results))]

    def render(self) -> str:
        return self.doc.render(self.texts())

    def flagged(self) -> list[int]:
        return [i for i, r in enumerate(self.results) if r.changed and r.risk >= 2]

    def unresolved(self) -> list[int]:
        return [i for i in self.flagged() if i not in self.overrides]

    @property
    def confidence(self) -> str:
        """'high' – fine to save without looking · 'check' – a few judgement calls · 'low' – needs a look."""
        n = len(self.unresolved())
        if n == 0:
            return "high"
        if n > max(12, self.stats.total * 0.03) or (self.notes.ocr_suspect and n > 4):
            return "low"
        return "check"


def clean_doc(doc: SubtitleDoc, opts: CleanOptions, plain: SubtitleDoc | None = None) -> CleanResult:
    dn, ctxs = analyse_doc(doc)
    results = [clean_cue(c.text, opts, doc.fmt, ctx) for c, ctx in zip(doc.cues, ctxs)]
    if plain is not None and plain.cues:
        if cross_check(doc, results, plain):
            dn.notes.append("Checked against the regular English subtitles in the same file.")
    st = CleanStats(total=len(results))
    for r in results:
        if not r.changed:
            continue
        if r.text is None:
            st.removed += 1
        else:
            st.edited += 1
        if r.risk >= 2:
            st.risky += 1
        elif r.risk == 1:
            st.glance += 1
        for rule in r.rules:
            st.by_rule[rule] = st.by_rule.get(rule, 0) + 1
    return CleanResult(doc, results, st, dn)


# ------------------------------------------------------------------------ answer-key check

_TOK = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


def _toks(s: str) -> list[str]:
    return _TOK.findall(s.lower().replace("’", "'").replace("‘", "'"))


def cross_check(doc: SubtitleDoc, results: list[CueResult], plain: SubtitleDoc) -> bool:
    """Flag removals that the plain (non-SDH) track of the same video disagrees with.

    If the regular subtitles have words at the same moment that we removed from the SDH
    track (and they aren't just a character's name), that removal is probably wrong.
    Returns False when the plain track is useless as a reference (it has SDH content itself).
    """
    sdh_like = sum(1 for c in plain.cues if re.search(r"\[[^\]]+\]|\([A-Z ]{4,}\)", c.text))
    if sdh_like > 0.02 * len(plain.cues):
        return False
    try:
        pts = sorted((to_seconds(c.start), to_seconds(c.end),
                      Counter(_toks(re.sub(r"\[[^\]]*\]|\([^a-z)]*\)", " ", visible_text(c.text, plain.fmt)))))
                     for c in plain.cues)
    except (ValueError, TypeError):
        return False
    starts = [p[0] for p in pts]
    for c, r in zip(doc.cues, results):
        if not r.changed:
            continue
        raw_vis = visible_text(c.text, doc.fmt)
        removed = Counter(_toks(raw_vis)) - Counter([] if r.text is None else _toks(visible_text(r.text, doc.fmt)))
        if not removed:
            continue
        cln = Counter([] if r.text is None else _toks(visible_text(r.text, doc.fmt)))
        s, e = to_seconds(c.start), to_seconds(c.end)
        j = bisect.bisect_left(starts, s - 10)
        win = Counter()
        while j < len(pts) and pts[j][0] < e + 0.25:
            if pts[j][1] > s - 0.25:
                win.update(pts[j][2])
            j += 1
        if not win:
            continue
        capitalised = {w.lower() for w in re.findall(r"\b[A-Z][\w'’]*", raw_vis)}
        hits = [w for w, n in removed.items()
                if min(n, max(0, win[w] - cln[w])) > 0 and w not in capitalised and not w.isdigit()]
        if len(hits) >= 2:
            r.risk = 2
            msg = "the regular English subtitles have “" + " ".join(hits[:4]) + "” here"
            if msg not in r.why:
                r.why.append(msg)
    return True


# ------------------------------------------------------------------------ preview helpers


def visible_text(text: str, fmt: str = "srt") -> str:
    """What a viewer would actually read (tags removed)."""
    tag_re = TAG_RE_ASS if fmt == "ass" else TAG_RE_TEXT
    t = tag_re.sub("", text)
    if fmt == "ass":
        t = t.replace("\\h", " ").replace("\\n", " ")
    return t.strip()


def diff_html(before: str, after: str | None) -> str:
    """Original text with removed parts marked <del>. Plain HTML fragments for the preview."""
    if after is None:
        return f"<del>{html.escape(before)}</del>"
    out = []
    sm = difflib.SequenceMatcher(None, before, after, autojunk=False)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        seg = before[a0:a1]
        if op == "equal":
            out.append(html.escape(seg))
        elif op in ("delete", "replace"):
            if seg.strip():
                out.append(f"<del>{html.escape(seg)}</del>")
            else:
                out.append(html.escape(seg))
    return "".join(out)
