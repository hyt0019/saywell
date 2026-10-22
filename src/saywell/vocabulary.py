"""Personal glossary plus imported professional lexicons.

An imported lexicon can hold tens of thousands of terms, far too many to send with every
request. Each transcript only brings along the imported terms that sound like part of it:
the same toneless pinyin written with other characters (龚玉晨 → 龚彧宸), an English term
written differently (deep seek → DeepSeek), or a misrecognition listed in a correction file.
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from itertools import islice, product
from pathlib import Path

from .settings import data_dir

PERSONAL_LIMIT = 100  # Every personal glossary word goes with every request.
RETRIEVED_LIMIT = 30  # Imported terms matched to one transcript.
LEXICON_LIMIT = 200_000  # Terms kept from one imported file.
TERM_LENGTH = 80
CONTEXT_CHARS = 1500  # Text context given to Qwen3-ASR for one segment.
# Two-character imported words collide with everyday speech (重要/中药, 费用/肺痈), so imported
# Chinese terms match from three characters; the user's own glossary words from two.
MIN_HAN = 3
MIN_PERSONAL_HAN = 2
MAX_HAN = 12
FILE_FILTER = "词库文件 (*.txt *.csv *.json *.scel *.yaml);;所有文件 (*)"

_HAN = "㐀-䶿一-鿿"
_HAN_RUN = re.compile(f"[{_HAN}]+")
_ASCII_TERM = re.compile(r"[A-Za-z][A-Za-z0-9.+#'\- ]*")
_ASCII_RUN = re.compile(r"[A-Za-z0-9]+(?:[ .\-][A-Za-z0-9]+)*")
_PINYIN_CODE = re.compile(r"'?[a-z]+(?:'[a-z]+)*'?")
_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")
_HEADERS = {
    "word", "words", "term", "terms", "phrase", "text", "vocabulary", "original", "replacement",
    "from", "to", "词", "词语", "词条", "热词", "术语", "原词", "替换", "错误", "正确",
}


class LexiconError(ValueError):
    pass


@dataclass
class ParsedLexicon:
    name: str
    terms: list[str] = field(default_factory=list)
    # (heard, written): a known misrecognition and the spelling it should become.
    corrections: list[tuple[str, str]] = field(default_factory=list)
    truncated: bool = False

    @property
    def count(self) -> int:
        return len(self.terms) + len(self.corrections)


def clean_term(value: str) -> str | None:
    term = " ".join(value.replace("　", " ").split()).strip("\"'“”‘’")
    if not term or len(term) > TERM_LENGTH or not re.search(f"[A-Za-z{_HAN}]", term):
        return None
    if any(ord(char) < 32 for char in term):
        return None
    return term


def _decode(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("gb18030")  # Text exported by Chinese input methods.
    except UnicodeDecodeError:
        raise LexiconError("无法识别文件编码，请另存为 UTF-8 后重试。") from None


def _text_terms(text: str) -> list[str]:
    """One term per line, with the weights, frequencies and pinyin columns that
    input methods, jieba, THUOCL and cloud hotword lists add around it."""
    # Baidu export: 阿里巴巴(a|li|ba|ba) 1
    lines = [re.sub(r"\([a-z|' ]+\)", "", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line and not line.startswith(("#", "//", ";"))]
    # Trailing numbers are weights only when (nearly) every line has one; otherwise
    # they belong to the term, as in "Windows 11".
    weighted = sum(bool(_NUMBER.fullmatch(line.split()[-1])) for line in lines) >= 0.8 * len(lines)
    terms = []
    for line in lines:
        if "\t" in line or "|" in line:
            fields = [part.strip() for part in re.split(r"[\t|]", line) if part.strip()]
            han = [part for part in fields if _HAN_RUN.search(part)]
            # "词\t频率", "词\t拼音\t权重", "拼音\t词" and "词|权重" all keep the word column.
            term = han[0] if han else fields[0]
        else:
            tokens = line.split()
            if len(tokens) >= 2 and _PINYIN_CODE.fullmatch(tokens[0]) and _HAN_RUN.search(tokens[1]):
                term = tokens[1]  # Sogou / QQ text export: 'a'li'ba'ba 阿里巴巴 [freq]
            elif _HAN_RUN.search(tokens[0]) and all(
                _NUMBER.fullmatch(token) or re.fullmatch(r"[a-z]{1,4}", token) for token in tokens[1:]
            ):
                term = tokens[0]  # jieba user dictionary: 词 频率 词性
            else:
                while weighted and len(tokens) > 1 and _NUMBER.fullmatch(tokens[-1]):
                    tokens.pop()
                term = " ".join(tokens)
        terms.append(term)
    return terms


def _csv_lexicon(text: str, parsed: ParsedLexicon) -> None:
    rows = [[cell.strip() for cell in row] for row in csv.reader(io.StringIO(text))]
    rows = [row for row in rows if any(row)]
    if rows and any(cell.lower() in _HEADERS for cell in rows[0]):
        rows = rows[1:]
    for row in rows:
        if len(row) >= 2 and row[0] and row[1] and not _NUMBER.fullmatch(row[1]):
            parsed.corrections.append((row[0], row[1]))  # Wispr Flow style: heard,written
        elif row[0]:
            parsed.terms.append(row[0])


def _json_terms(text: str) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        raise LexiconError("JSON 格式有误，无法读取。") from None
    if isinstance(data, dict):
        data = next((data[key] for key in ("vocabulary", "words", "hotwords", "terms") if key in data), None)
    if not isinstance(data, list):
        raise LexiconError("JSON 里没有找到词表（应为词语列表，或带 text 字段的对象列表）。")
    terms = []
    for item in data:
        if isinstance(item, dict):  # Alibaba Cloud hotwords: {"text": ..., "weight": ...}
            item = next((item[key] for key in ("text", "word", "term") if isinstance(item.get(key), str)), "")
        if isinstance(item, str):
            terms.append(item)
    return terms


def _rime_terms(text: str) -> list[str]:
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "...")
    except StopIteration:
        return _text_terms(text)
    column = 0
    header = "\n".join(lines[:start])
    columns = re.search(r"^columns:\s*\n((?:\s*-\s*\w+\s*\n?)+)", header, re.M)
    if columns:
        names = re.findall(r"-\s*(\w+)", columns.group(1))
        column = names.index("text") if "text" in names else 0
    terms = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.lstrip().startswith("#"):
            fields = line.split("\t")
            if column < len(fields):
                terms.append(fields[column])
    return terms


def _utf16(data: bytes) -> str:
    return data.decode("utf-16-le", "ignore").split("\x00", 1)[0].strip()


def _scel(data: bytes) -> tuple[str, list[str]]:
    """Sogou cell dictionary: a UTF-16 name at 0x130, a pinyin table from 0x1540, then groups
    of homophones. Layout as documented by the ciku and imewlconverter projects."""
    if len(data) < 0x1544 or data[:1] != b"@":
        raise LexiconError("不是有效的搜狗细胞词库（.scel）文件。")

    def u16(at: int) -> int:
        return int.from_bytes(data[at : at + 2], "little")

    pos = 0x1544
    for _ in range(u16(0x1540)):
        if pos + 4 > len(data):
            raise LexiconError("搜狗词库文件不完整。")
        pos += 4 + u16(pos + 2)
    # A trailing DELTBL block lists deleted words without pinyin; they are not terms.
    ends = [data.find(mark, pos) for mark in (b"DELTBL", "DELTBL".encode("utf-16-le"))]
    end = min([at for at in ends if at >= 0], default=len(data))
    words = []
    while pos + 4 <= end:
        homophones, code = u16(pos), u16(pos + 2)
        pos += 4 + code
        for _ in range(homophones):
            if pos + 2 > end:
                break
            size = u16(pos)
            words.append(_utf16(data[pos + 2 : pos + 2 + size]))
            pos += 2 + size
            if pos + 2 > end:
                break
            pos += 2 + u16(pos)  # Extension: frequency and reserved bytes.
    return _utf16(data[0x130:0x338]), words


def read_lexicon(path: Path) -> ParsedLexicon:
    data = path.read_bytes()
    name = path.name.removesuffix(".dict.yaml") if path.name.endswith(".dict.yaml") else path.stem
    suffix = path.suffix.lower()
    parsed = ParsedLexicon(name)
    if suffix == ".scel" or data[:1] == b"@" and suffix not in (".txt", ".csv", ".json", ".yaml"):
        title, parsed.terms = _scel(data)
        parsed.name = title or name
    else:
        text = _decode(data)
        if suffix == ".csv":
            _csv_lexicon(text, parsed)
        elif suffix == ".json":
            parsed.terms = _json_terms(text)
        elif suffix == ".yaml":
            parsed.terms = _rime_terms(text)
        else:
            parsed.terms = _text_terms(text)
    parsed.terms = list(dict.fromkeys(term for term in map(clean_term, parsed.terms) if term))
    parsed.corrections = list(
        dict.fromkeys(
            (heard, written)
            for heard, written in ((clean_term(a), clean_term(b)) for a, b in parsed.corrections)
            if heard and written and heard != written
        )
    )
    if parsed.count > LEXICON_LIMIT:
        parsed.truncated = True
        parsed.corrections = parsed.corrections[:LEXICON_LIMIT]
        parsed.terms = parsed.terms[: LEXICON_LIMIT - len(parsed.corrections)]
    if not parsed.count:
        raise LexiconError("没有从文件中读到词语，请确认是支持的词库格式。")
    return parsed


def lexicon_dir() -> Path:
    return data_dir() / "lexicons"


def lexicon_path(lexicon_id: str) -> Path:
    return lexicon_dir() / f"{lexicon_id}.txt"


def import_lexicon(path: Path) -> tuple[dict, ParsedLexicon]:
    """Copy a lexicon into the app's own format; returns its settings entry."""
    parsed = read_lexicon(path)
    lexicon_id = uuid.uuid4().hex
    target = lexicon_path(lexicon_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = parsed.terms + [f"{heard}\t{written}" for heard, written in parsed.corrections]
    temp = target.with_suffix(".tmp")
    temp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temp.replace(target)
    return {"id": lexicon_id, "name": parsed.name[:TERM_LENGTH], "enabled": True, "count": parsed.count}, parsed


def delete_lexicons(ids) -> None:
    """Delete the files of lexicons that were removed, or imported and then not saved.

    Only named ids are touched, so settings that failed to load can never wipe the folder."""
    for lexicon_id in ids:
        try:
            lexicon_path(lexicon_id).unlink(missing_ok=True)
        except OSError:
            pass  # Still in use; it is only a stray file now.


def _ascii_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class TermIndex:
    """Finds imported terms that sound like, or are a misspelling of, part of a transcript."""

    def __init__(self, min_han: int = MIN_HAN):
        self.min_han = min_han
        self.sounds: dict[tuple[str, ...], list[str]] = {}
        self.spellings: dict[str, str] = {}
        self.heard: dict[str, str] = {}

    @classmethod
    def build(cls, terms=(), corrections=(), min_han: int = MIN_HAN) -> TermIndex:
        from pypinyin import Style, pinyin

        index = cls(min_han)
        han_term = re.compile(f"[{_HAN}]{{{min_han},{MAX_HAN}}}")
        for term in terms:
            if han_term.fullmatch(term):
                # Index every reading of polyphonic characters (珩: heng/hang, 栎: li/yue).
                readings = pinyin(term, style=Style.NORMAL, heteronym=True, errors="ignore")
                if len(readings) == len(term):
                    for sound in islice(product(*readings), 16):
                        index.sounds.setdefault(sound, []).append(term)
            elif _ASCII_TERM.fullmatch(term):
                key = _ascii_key(term)
                if len(key) >= 4:
                    index.spellings.setdefault(key, term)
            # Mixed terms such as "B超" have neither a pinyin nor an English key.
        for heard, written in corrections:
            index.heard.setdefault(heard, written)
        return index

    def __bool__(self) -> bool:
        return bool(self.sounds or self.spellings or self.heard)

    def lookup(self, text: str, limit: int = RETRIEVED_LIMIT) -> list[str]:
        from pypinyin import lazy_pinyin

        found: dict[str, int] = {}
        for heard, written in self.heard.items():
            at = text.find(heard)
            if at >= 0:
                found.setdefault(written, at)
        if self.sounds:
            for run in _HAN_RUN.finditer(text):
                chars = run.group()
                sound = lazy_pinyin(chars)
                for size in range(self.min_han, min(MAX_HAN, len(chars)) + 1):
                    for i in range(len(chars) - size + 1):
                        for term in self.sounds.get(tuple(sound[i : i + size]), ()):
                            if term != chars[i : i + size]:
                                found.setdefault(term, run.start() + i)
        if self.spellings:
            for run in _ASCII_RUN.finditer(text):
                words = list(re.finditer(r"[A-Za-z0-9]+", run.group()))
                for size in (1, 2, 3):
                    for i in range(len(words) - size + 1):
                        part = words[i : i + size]
                        written = self.spellings.get("".join(word.group().lower() for word in part))
                        surface = run.group()[part[0].start() : part[-1].end()]
                        if written and written != surface:
                            found.setdefault(written, run.start() + part[0].start())
        return [term for term, _ in sorted(found.items(), key=lambda item: item[1])][:limit]


def read_terms(lexicon_id: str) -> tuple[list[str], list[tuple[str, str]]]:
    terms, corrections = [], []
    for line in lexicon_path(lexicon_id).read_text(encoding="utf-8").splitlines():
        if "\t" in line:
            heard, written = line.split("\t", 1)
            corrections.append((heard, written))
        elif line:
            terms.append(line)
    return terms, corrections


_cache_lock = threading.Lock()
_cache: tuple[tuple, TermIndex | None] | None = None


def lexicon_index(lexicons: list[dict]) -> TermIndex | None:
    """The index of the enabled lexicons, built once and reused until they change."""
    global _cache
    enabled = [item for item in lexicons if item.get("enabled")]
    if not enabled:
        return None
    signature = []
    for item in enabled:
        try:
            stat = lexicon_path(item["id"]).stat()
            signature.append((item["id"], stat.st_mtime_ns, stat.st_size))
        except OSError:
            continue  # A missing file only drops that lexicon.
    key = tuple(signature)
    with _cache_lock:  # Concurrent callers wait for the one build instead of repeating it.
        if _cache and _cache[0] == key:
            return _cache[1]
        terms, corrections = [], []
        for lexicon_id, _mtime, _size in key:
            try:
                more, fixes = read_terms(lexicon_id)
            except OSError:
                continue
            terms += more
            corrections += fixes
        index = TermIndex.build(terms, corrections) if terms or corrections else None
        _cache = (key, index)
        return index


def warm(lexicons: list[dict]) -> None:
    """Build the index in the background so the first dictation does not wait for it."""
    if any(item.get("enabled") for item in lexicons):
        threading.Thread(target=lexicon_index, args=(list(lexicons),), daemon=True).start()


_personal_cache: dict[tuple[str, ...], TermIndex] = {}


def personal_index(words: list[str]) -> TermIndex:
    key = tuple(words)
    if key not in _personal_cache:
        _personal_cache.clear()  # Only the current glossary is worth keeping.
        _personal_cache[key] = TermIndex.build(words, min_han=MIN_PERSONAL_HAN)
    return _personal_cache[key]


@dataclass
class Vocabulary:
    personal: list[str] = field(default_factory=list)
    index: TermIndex | None = None

    @classmethod
    def for_settings(cls, settings) -> Vocabulary:
        return cls(list(settings.glossary[:PERSONAL_LIMIT]), lexicon_index(settings.lexicons))

    def lookup(self, text: str) -> list[str]:
        """Imported terms that sound like part of text and are not already personal words."""
        if not self.index:
            return []
        personal = set(self.personal)
        matches = self.index.lookup(text, RETRIEVED_LIMIT + len(personal))
        return [term for term in matches if term not in personal][:RETRIEVED_LIMIT]

    def matches(self, text: str) -> list[str]:
        """Personal and imported terms that sound like part of text, for Qwen3-ASR to hear again.

        Any context makes Qwen3-ASR space out English words, so segments without a match get none."""
        own = personal_index(self.personal).lookup(text) if self.personal else []
        return list(dict.fromkeys(own + self.lookup(text)))[:RETRIEVED_LIMIT]

    def glossary_for(self, text: str) -> list[str]:
        """Words sent with one cleanup request: the personal glossary plus matched terms."""
        return self.personal + self.lookup(text)

    @staticmethod
    def context(terms: list[str]) -> str:
        """Text context for Qwen3-ASR, kept well inside the engine's context window."""
        text = ""
        for term in terms:
            if len(text) + len(term) + 1 > CONTEXT_CHARS:
                break
            text = f"{text} {term}" if text else term
        return text
