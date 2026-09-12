"""Inference text cleaners from the model-matched classic VITS Space.

Only cleaners used by inference are retained; Korean training helpers are omitted.
"""

import re

import cn2an
import jieba
import pyopenjtalk
from pypinyin import BOPOMOFO, lazy_pinyin
from unidecode import unidecode


_whitespace_re = re.compile(r"\s+")
_japanese_characters = re.compile(
    r"[A-Za-z\d\u3005\u3040-\u30ff\u4e00-\u9fff\uff11-\uff19\uff21-\uff3a\uff41-\uff5a\uff66-\uff9d]"
)
_japanese_marks = re.compile(
    r"[^A-Za-z\d\u3005\u3040-\u30ff\u4e00-\u9fff\uff11-\uff19\uff21-\uff3a\uff41-\uff5a\uff66-\uff9d]"
)

_latin_to_bopomofo = [
    (re.compile(f"{source}", re.IGNORECASE), target)
    for source, target in [
        ("a", "ㄟˉ"), ("b", "ㄅㄧˋ"), ("c", "ㄙㄧˉ"), ("d", "ㄉㄧˋ"),
        ("e", "ㄧˋ"), ("f", "ㄝˊㄈㄨˋ"), ("g", "ㄐㄧˋ"), ("h", "ㄝˇㄑㄩˋ"),
        ("i", "ㄞˋ"), ("j", "ㄐㄟˋ"), ("k", "ㄎㄟˋ"), ("l", "ㄝˊㄛˋ"),
        ("m", "ㄝˊㄇㄨˋ"), ("n", "ㄣˉ"), ("o", "ㄡˉ"), ("p", "ㄆㄧˉ"),
        ("q", "ㄎㄧㄡˉ"), ("r", "ㄚˋ"), ("s", "ㄝˊㄙˋ"), ("t", "ㄊㄧˋ"),
        ("u", "ㄧㄡˉ"), ("v", "ㄨㄧˉ"), ("w", "ㄉㄚˋㄅㄨˋㄌㄧㄡˋ"),
        ("x", "ㄝˉㄎㄨˋㄙˋ"), ("y", "ㄨㄞˋ"), ("z", "ㄗㄟˋ"),
    ]
]

_bopomofo_to_romaji = [
    (re.compile(source, re.IGNORECASE), target)
    for source, target in [
        ("ㄅㄛ", "p⁼wo"), ("ㄆㄛ", "pʰwo"), ("ㄇㄛ", "mwo"), ("ㄈㄛ", "fwo"),
        ("ㄅ", "p⁼"), ("ㄆ", "pʰ"), ("ㄇ", "m"), ("ㄈ", "f"),
        ("ㄉ", "t⁼"), ("ㄊ", "tʰ"), ("ㄋ", "n"), ("ㄌ", "l"),
        ("ㄍ", "k⁼"), ("ㄎ", "kʰ"), ("ㄏ", "h"), ("ㄐ", "ʧ⁼"),
        ("ㄑ", "ʧʰ"), ("ㄒ", "ʃ"), ("ㄓ", "ʦ`⁼"), ("ㄔ", "ʦ`ʰ"),
        ("ㄕ", "s`"), ("ㄖ", "ɹ`"), ("ㄗ", "ʦ⁼"), ("ㄘ", "ʦʰ"),
        ("ㄙ", "s"), ("ㄚ", "a"), ("ㄛ", "o"), ("ㄜ", "ə"),
        ("ㄝ", "e"), ("ㄞ", "ai"), ("ㄟ", "ei"), ("ㄠ", "au"),
        ("ㄡ", "ou"), ("ㄧㄢ", "yeNN"), ("ㄢ", "aNN"), ("ㄧㄣ", "iNN"),
        ("ㄣ", "əNN"), ("ㄤ", "aNg"), ("ㄧㄥ", "iNg"), ("ㄨㄥ", "uNg"),
        ("ㄩㄥ", "yuNg"), ("ㄥ", "əNg"), ("ㄦ", "əɻ"), ("ㄧ", "i"),
        ("ㄨ", "u"), ("ㄩ", "ɥ"), ("ˉ", "→"), ("ˊ", "↑"),
        ("ˇ", "↓↑"), ("ˋ", "↓"), ("˙", ""), ("，", ","),
        ("。", "."), ("！", "!"), ("？", "?"), ("—", "-"),
    ]
]


def lowercase(text):
    return text.lower()


def collapse_whitespace(text):
    return re.sub(_whitespace_re, " ", text)


def convert_to_ascii(text):
    return unidecode(text)


def basic_cleaners(text):
    return collapse_whitespace(lowercase(text))


def transliteration_cleaners(text):
    return collapse_whitespace(lowercase(convert_to_ascii(text)))


def japanese_to_romaji_with_accent(text):
    """Reference implementation used by the upstream model frontend."""
    sentences = re.split(_japanese_marks, text)
    marks = re.findall(_japanese_marks, text)
    output = ""
    for i, sentence in enumerate(sentences):
        if re.match(_japanese_characters, sentence):
            if output != "":
                output += " "
            labels = pyopenjtalk.extract_fullcontext(sentence)
            for n, label in enumerate(labels):
                phoneme = re.search(r"\-([^\+]*)\+", label).group(1)
                if phoneme not in ["sil", "pau"]:
                    output += phoneme.replace("ch", "ʧ").replace("sh", "ʃ").replace("cl", "Q")
                else:
                    continue
                n_moras = int(re.search(r"/F:(\d+)_", label).group(1))
                a1 = int(re.search(r"/A:(\-?[0-9]+)\+", label).group(1))
                a2 = int(re.search(r"\+(\d+)\+", label).group(1))
                a3 = int(re.search(r"\+(\d+)/", label).group(1))
                if re.search(r"\-([^\+]*)\+", labels[n + 1]).group(1) in ["sil", "pau"]:
                    a2_next = -1
                else:
                    a2_next = int(re.search(r"\+(\d+)\+", labels[n + 1]).group(1))
                if a3 == 1 and a2_next == 1:
                    output += " "
                elif a1 == 0 and a2_next == a2 + 1 and a2 != n_moras:
                    output += "↓"
                elif a2 == 1 and a2_next == 2:
                    output += "↑"
        if i < len(marks):
            output += unidecode(marks[i]).replace(" ", "")
    return output


def number_to_chinese(text):
    numbers = re.findall(r"\d+(?:\.?\d+)?", text)
    for number in numbers:
        text = text.replace(number, cn2an.an2cn(number), 1)
    return text


def chinese_to_bopomofo(text):
    text = text.replace("、", "，").replace("；", "，").replace("：", "，")
    words = jieba.lcut(text, cut_all=False)
    output = ""
    for word in words:
        bopomofos = lazy_pinyin(word, BOPOMOFO)
        if not re.search(r"[\u4e00-\u9fff]", word):
            output += word
            continue
        for i in range(len(bopomofos)):
            if re.match(r"[\u3105-\u3129]", bopomofos[i][-1]):
                bopomofos[i] += "ˉ"
        if output != "":
            output += " "
        output += "".join(bopomofos)
    return output


def latin_to_bopomofo(text):
    for regex, replacement in _latin_to_bopomofo:
        text = re.sub(regex, replacement, text)
    return text


def bopomofo_to_romaji(text):
    for regex, replacement in _bopomofo_to_romaji:
        text = re.sub(regex, replacement, text)
    return text


def japanese_cleaners(text):
    text = japanese_to_romaji_with_accent(text)
    if text and re.match(r"[A-Za-z]", text[-1]):
        text += "."
    return text


def japanese_cleaners2(text):
    return japanese_cleaners(text).replace("ts", "ʦ").replace("...", "…")


def chinese_cleaners(text):
    text = number_to_chinese(text)
    text = chinese_to_bopomofo(text)
    text = latin_to_bopomofo(text)
    if text and re.match(r"[ˉˊˇˋ˙]", text[-1]):
        text += "。"
    return text


def zh_ja_mixture_cleaners(text):
    chinese_texts = re.findall(r"\[ZH\].*?\[ZH\]", text)
    japanese_texts = re.findall(r"\[JA\].*?\[JA\]", text)
    for chinese_text in chinese_texts:
        cleaned_text = number_to_chinese(chinese_text[4:-4])
        cleaned_text = chinese_to_bopomofo(cleaned_text)
        cleaned_text = latin_to_bopomofo(cleaned_text)
        cleaned_text = bopomofo_to_romaji(cleaned_text)
        cleaned_text = re.sub(r"i[aoe]", lambda x: "y" + x.group(0)[1:], cleaned_text)
        cleaned_text = re.sub(r"u[aoəe]", lambda x: "w" + x.group(0)[1:], cleaned_text)
        cleaned_text = re.sub(
            r"([ʦsɹ]`[⁼ʰ]?)([→↓↑]+)",
            lambda x: x.group(1) + "ɹ`" + x.group(2),
            cleaned_text,
        ).replace("ɻ", "ɹ`")
        cleaned_text = re.sub(
            r"([ʦs][⁼ʰ]?)([→↓↑]+)",
            lambda x: x.group(1) + "ɹ" + x.group(2),
            cleaned_text,
        )
        text = text.replace(chinese_text, cleaned_text + " ", 1)
    for japanese_text in japanese_texts:
        cleaned_text = (
            japanese_to_romaji_with_accent(japanese_text[4:-4])
            .replace("ts", "ʦ")
            .replace("u", "ɯ")
            .replace("...", "…")
        )
        text = text.replace(japanese_text, cleaned_text + " ", 1)
    if text:
        text = text[:-1]
    if text and re.match(r"[A-Za-zɯɹəɥ→↓↑]", text[-1]):
        text += "."
    return text
