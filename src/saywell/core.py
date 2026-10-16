from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field


def single_line_text(text: str) -> str:
    """Keep words separated while removing line breaks and terminal control characters."""
    return re.sub(r"[\s\x00-\x1f\x7f-\x9f]+", " ", text).strip()


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


@dataclass(frozen=True)
class CleanupResult:
    original: str
    text: str
    warnings: tuple[str, ...] = ()
    edits: tuple[Edit, ...] = ()
    elapsed: float = 0.0
    usage: dict = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "text", single_line_text(self.text))

    @property
    def needs_review(self) -> bool:
        return bool(self.warnings)


_NEGATION = re.compile(r"不要|不能|不必|不用|不是|没有|不会|不想|不需要|不得|不允许|别|未|不")
_NUMBERS = re.compile(r"\d+(?:[.,]\d+)*|[零〇一二两三四五六七八九十百千万亿]+")
_CORRECTION = re.compile(r"不对|说错|改成|改为|改到|改用|改周|不是.{0,12}是|算了|还是.{0,20}吧")
_HEDGE = ("可能", "不确定", "没确定", "或者", "大概", "也许", "暂时", "先不用", "不能超过")
_UNRESOLVED = re.compile(
    r"(?:^|[，。！？])\s*(?:不对[，。]|说错了[，。]|算了[，。]|不是[，,]\s*是)|前面[^。！？]{0,12}说错"
)


def _plain(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:‘’“”\"'（）()]+", "", text).lower()


def _number(value: str) -> str:
    """Normalize common spoken Chinese integers, without interpreting dates/units."""
    if value[0].isdigit():
        return value.replace(",", "")
    digits = dict(zip("零〇一二两三四五六七八九", [0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9], strict=True))
    units = {"十": 10, "百": 100, "千": 1000, "万": 10000, "亿": 100000000}
    if all(ch in digits for ch in value):
        return "".join(str(digits[ch]) for ch in value)
    total = section = current = 0
    for ch in value:
        if ch in digits:
            current = digits[ch]
        elif units[ch] < 10000:
            section += (current or 1) * units[ch]
            current = 0
        else:
            section = (section + current) * units[ch]
            total += section
            section = current = 0
    return str(total + section + current)


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
    source_numbers = {_number(x) for x in _NUMBERS.findall(original)}
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
