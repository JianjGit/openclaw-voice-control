from __future__ import annotations

import re


EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE,
)


def clean_text(text: str) -> str:
    if not text:
        return ""

    value = text.strip()
    value = re.sub(r"```[\s\S]*?```", " ", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", value)
    value = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", value)
    value = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", value)
    value = re.sub(r"(?m)^\s*>\s?", "", value)
    value = re.sub(r"(?m)^\s*[-*+]\s+", "", value)
    value = re.sub(r"(?m)^\s*\d+\.\s+", "", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"\1", value)
    value = re.sub(r"\*([^*]+)\*", r"\1", value)
    value = re.sub(r"__([^_]+)__", r"\1", value)
    value = re.sub(r"_([^_]+)_", r"\1", value)
    value = value.replace("#", "")
    value = value.replace("*", "")
    value = value.replace("|", " ")
    value = value.replace("•", " ")
    value = EMOJI_PATTERN.sub(" ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def clean_text_for_tts(text: str) -> str:
    value = clean_text(text)
    if not value:
        return ""

    value = value.replace("\n", "。")
    value = re.sub(r"[。]{2,}", "。", value)
    value = re.sub(r"[，]{2,}", "，", value)
    value = re.sub(r"\s+", " ", value)
    value = value.strip(" \n\t。")
    if not value:
        return ""
    return value + "。"
