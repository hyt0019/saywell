from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from math import prod

from pydantic import BaseModel, ConfigDict, Field


def single_line_text(text: str) -> str:
    """Keep words separated while removing line breaks and terminal control characters."""
    return re.sub(r"[\s\x00-\x1f\x7f-\x9f]+", " ", text).strip()


# Chinese characters and Japanese kana. Korean is left alone: it separates words with spaces.
_HAN = "㐀-䶿一-鿿豈-﫿぀-ヿ"
# Chinese and full-width punctuation, including the curly quotes and the ellipsis.
_CJK_MARKS = "　-〿！-／：-＠“”‘’…"
_MIXED_SPACE = re.compile(
    rf"(?<=[{_HAN}{_CJK_MARKS}])[ \t]+(?=[A-Za-z0-9#$@])|(?<=[A-Za-z0-9%+#)])[ \t]+(?=[{_HAN}{_CJK_MARKS}])"
)
# Taken out for an unpunctuated paragraph. A single dash or tilde stays: it can mean "to" (3～5).
_MARKS = re.compile(r"\s*(?:——|[，。！？；：、“”‘’（）《》〈〉【】〔〕〖〗「」『』…])+\s*")
# English sentence punctuation. Apostrophes, hyphens and parentheses stay: don't, e-mail, f(x).
_EN_MARKS = re.compile(r'[,.!?;:"]+')
# Fillers said on their own between pauses ("嗯，", "呃……"); a word such as 额度 is never touched.
_FILLER = re.compile(r"(?:^|(?<=[，。！？；：、…]))\s*(?:嗯|呃|额|唔|诶|欸|哎|啊|哦|噢)+\s*(?:[，。！？；：、…]+|$)")


def tighten_mixed_spacing(text: str) -> str:
    """Drop the spaces around English words and numbers in Chinese text ("用 API 调用" ->
    "用API调用"). Text without Chinese, or mostly English with a Chinese name in it, keeps them."""
    han = len(re.findall(f"[{_HAN}]", text))
    chinese = re.search(f"[{_CJK_MARKS}]", text) or han >= len(re.findall(r"[A-Za-z]+", text))
    return _MIXED_SPACE.sub("", text) if han and chinese else text


def strip_punctuation(text: str) -> str:
    """One unpunctuated paragraph: drop Chinese punctuation and standalone fillers. A mark between
    two English words or numbers becomes a space; English punctuation and 3.5 stay."""

    def drop(match: re.Match) -> str:
        before = match.string[match.start() - 1 : match.start()]
        after = match.string[match.end() : match.end() + 1]
        return " " if (before + after).isascii() and before.isalnum() and after.isalnum() else ""

    return _MARKS.sub(drop, _FILLER.sub("", text)).strip()


def strip_english_punctuation(text: str) -> str:
    """Drop English sentence punctuation. A mark with no space on either side stays, so 3.5,
    1,000, 10:30, https://saywell.app and the inner dots of U.S.A keep their meaning."""

    def drop(match: re.Match) -> str:
        before = match.string[match.start() - 1 : match.start()]
        after = match.string[match.end() : match.end() + 1]
        inside = (before + after).isascii() and before.strip() and after.strip()
        return match.group() if inside else ""

    return re.sub(r" {2,}", " ", _EN_MARKS.sub(drop, text)).strip()


def finish_text(text: str, no_punctuation: bool = False, no_english_punctuation: bool = False) -> str:
    """The last step for every result before it is shown and typed. Spacing is decided first,
    while the punctuation still tells Chinese sentences from English ones. A space that stood
    after an English mark ("different. 大家") goes once the mark does."""
    text = tighten_mixed_spacing(text)
    text = strip_punctuation(text) if no_punctuation else text
    return tighten_mixed_spacing(strip_english_punctuation(text)) if no_english_punctuation else text


class Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=4000)
    replacement: str = Field(max_length=4000)
    evidence: str = Field(min_length=1, max_length=4000)


class CleanupPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20000)
    edits: list[Edit] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    # Target language when the speaker ended with "翻译成英文" and the like; empty otherwise.
    translated_to: str = Field(default="", max_length=40)


class TranslationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20000)


@dataclass(frozen=True)
class CleanupResult:
    original: str
    text: str
    warnings: tuple[str, ...] = ()
    edits: tuple[Edit, ...] = ()
    elapsed: float = 0.0
    usage: dict = field(default_factory=dict)
    translated_to: str = ""

    def __post_init__(self):
        object.__setattr__(self, "text", single_line_text(self.text))

    @property
    def needs_review(self) -> bool:
        return bool(self.warnings)


_NEGATION = re.compile(r"不要|不能|不必|不用|不是|没有|不会|不想|不需要|不得|不允许|别|未|不")
_DIGITS = dict(zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9], strict=True))
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
# Arabic numbers (3.5, 1,000, 5万) and spoken Chinese ones, reading 三点五 as a decimal.
_NUMBERS = re.compile(r"\d+(?:[.,]\d+)*[百千万亿]*|[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+[万亿]*)?")
# Without decimals, for the original: 三点五十 may also be the time 3点50.
_WHOLE_NUMBERS = re.compile(r"\d+(?:[.,]\d+)*[百千万亿]*|[零〇一二两三四五六七八九十百千万亿]+")
_CORRECTION = re.compile(r"不对|说错|改成|改为|改到|改用|改周|不是.{0,12}是|算了|还是.{0,20}吧")
_HEDGE = ("可能", "不确定", "没确定", "或者", "大概", "也许", "暂时", "先不用", "不能超过")
_UNRESOLVED = re.compile(
    r"(?:^|[，。！？])\s*(?:不对[，。]|说错了[，。]|算了[，。]|不是[，,]\s*是)|前面[^。！？]{0,12}说错"
)


def _plain(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:‘’“”\"'（）()]+", "", text).lower()


def _chinese_integer(value: str) -> int:
    """A spoken Chinese integer, including 二零二六 read digit by digit and 两万五 for 25000."""
    if all(ch in _DIGITS for ch in value):
        return int("".join(str(_DIGITS[ch]) for ch in value))
    total = section = current = 0
    for ch in value:
        if ch in _DIGITS:
            current = _DIGITS[ch]
        elif _UNITS[ch] < 10000:
            section += (current or 1) * _UNITS[ch]
            current = 0
        else:
            section = (section + current) * _UNITS[ch]
            total += section
            section = current = 0
    # 三千五 is 3500: a last digit right after 百 or a larger unit counts one place lower.
    if len(value) > 1 and value[-1] in _DIGITS and _UNITS.get(value[-2], 0) >= 100:
        current *= _UNITS[value[-2]] // 10
    return total + section + current


def _number(value: str) -> str:
    """The value a number stands for, so 三点五 and 3.5 match, and 五万, 5万 and 50000 do.
    Something like the version 1.2.3 is compared as written."""
    if value[0].isdigit():
        digits = value.rstrip("百千万亿")
        try:
            number = Decimal(digits.replace(",", "")) * prod(_UNITS[ch] for ch in value[len(digits) :])
        except InvalidOperation:
            return value
    elif "点" in value:
        whole, _, fraction = value.partition("点")
        digits = fraction.rstrip("万亿")
        number = _chinese_integer(whole) + Decimal("0." + "".join(str(_DIGITS[ch]) for ch in digits))
        number *= prod(_UNITS[ch] for ch in fraction[len(digits) :])
    else:
        number = Decimal(_chinese_integer(value))
    return format(number.normalize(), "f")


def validate_cleanup(original: str, payload: CleanupPayload) -> tuple[str, ...]:
    warnings = list(payload.warnings)
    source = _plain(original)
    target = _plain(payload.text)
    valid_edits = []
    for edit in payload.edits:
        if edit.source not in original or edit.evidence not in original:
            warnings.append("有修改缺少可核对的原文依据，请检查。")
        else:
            valid_edits.append(edit)
    if payload.translated_to:
        # The checks below compare Chinese wording (negations, hedges, length, rewrites);
        # a translation differs from the original by design, so they would only raise false alarms.
        return tuple(dict.fromkeys(warnings))
    source_numbers = {_number(x) for pattern in (_NUMBERS, _WHOLE_NUMBERS) for x in pattern.findall(original)}
    target_numbers = {_number(x) for x in _NUMBERS.findall(payload.text)}
    if target_numbers - source_numbers:
        warnings.append("整理结果出现了原文中未找到的数字，请核对。")
    if re.search(r"不是\s*(?:\d|[零〇一二两三四五六七八九十百千万])", original):
        warnings.append("原文有“不是＋数字”的表达，可能是识别遗漏了改口停顿，请核对数字。")
    correction = bool(_CORRECTION.search(original))
    unquoted = re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"', "", payload.text)
    if correction and _UNRESOLVED.search(unquoted):
        warnings.append("结果中仍有改口表达，请核对是否已经完成更正。")
    protected = original
    for edit in valid_edits:
        if _CORRECTION.search(edit.evidence):
            protected = protected.replace(edit.source, edit.replacement, 1)
    protected = re.sub(r"不对|不是[，,]\s*是", "", protected)
    if len(_NEGATION.findall(payload.text)) < len(_NEGATION.findall(protected)):
        warnings.append("否定表达发生变化，请核对原意。")
    for hedge in _HEDGE:
        if hedge in original and hedge not in payload.text:
            explained = any(
                hedge in edit.source and _CORRECTION.search(edit.evidence) for edit in valid_edits
            )
            if not explained:
                warnings.append(f"请核对“{hedge}”表达的条件或不确定性是否保留。")
    if len(source) >= 20 and len(target) < len(source) * 0.45:
        warnings.append("本次删减较多，请确认没有遗漏有效内容。")
    if len(target) > max(len(source) * 1.45, len(source) + 30):
        warnings.append("整理结果增加了较多内容，请核对。")
    matcher = difflib.SequenceMatcher(a=source, b=target, autojunk=False)
    for tag, _, _, start, end in matcher.get_opcodes():
        if tag in ("insert", "replace") and end - start >= 12:
            changed = target[start:end]
            if changed not in source:
                warnings.append("存在较大幅度的改写，请核对。")
                break
    return tuple(dict.fromkeys(warnings))
