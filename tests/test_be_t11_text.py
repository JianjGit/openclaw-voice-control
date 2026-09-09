from __future__ import annotations

from openclaw_voice_control.text import clean_text, clean_text_for_tts


def test_be_t11_clean_text_removes_markdown_links_code_and_emoji() -> None:
    source = "# 标题\n- **你好** [OpenClaw](https://example.com) `code` 😀"
    cleaned = clean_text(source)
    assert "#" not in cleaned
    assert "**" not in cleaned
    assert "https://" not in cleaned
    assert "😀" not in cleaned
    assert "标题" in cleaned
    assert "你好" in cleaned
    assert "OpenClaw" in cleaned
    assert "code" in cleaned


def test_be_t11_clean_text_for_tts_normalizes_lines_and_terminal_punctuation() -> None:
    assert clean_text_for_tts("第一行\n第二行") == "第一行。第二行。"
    assert clean_text_for_tts("  ") == ""
    assert clean_text_for_tts("你好。。") == "你好。"
