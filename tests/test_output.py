import pytest

from saywell.core import CleanupResult, single_line_text


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("第一句。\r\n第二句。\n第三句。\r最后一句。\n", "第一句。 第二句。 第三句。 最后一句。"),
        ("one\t\t two\vthree\ffour\x85five\u2028six\u2029seven", "one two three four five six seven"),
        ("\x1b\x00echo\x03 test\x7f\x9b\r", "echo test"),
        ("\r\n\t\x00\x1b", ""),
        (r"保留 literal \n、C:\new 和 👩‍💻，不要删除。", r"保留 literal \n、C:\new 和 👩‍💻，不要删除。"),
    ],
)
def test_single_line_output(source, expected):
    assert single_line_text(source) == expected
    result = CleanupResult(source, source)
    assert result.text == expected
    assert result.original == source  # Preserve the original for comparison.

