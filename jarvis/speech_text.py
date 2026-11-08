"""Turn Claude's streamed text into clean, speakable sentences."""
from __future__ import annotations

import re


class TagStripper:
    """Pass streamed text through, hiding [[...]] tags even when split across chunks."""

    def __init__(self) -> None:
        self.buf = ""

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        out: list[str] = []
        while True:
            i = self.buf.find("[[")
            if i == -1:
                if self.buf.endswith("["):          # could be the start of "[["
                    out.append(self.buf[:-1])
                    self.buf = "["
                else:
                    out.append(self.buf)
                    self.buf = ""
                break
            out.append(self.buf[:i])
            j = self.buf.find("]]", i)
            if j == -1:
                self.buf = self.buf[i:]
                break
            self.buf = self.buf[j + 2:]
        return "".join(out)

    def flush(self) -> str:
        rest, self.buf = self.buf, ""
        return "" if rest.startswith("[[") else rest


_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+|\n+")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_URL = re.compile(r"https?://\S+")
_LIST_MARK = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.M)
_HEADING = re.compile(r"^\s*#+\s*", re.M)


def clean_for_speech(text: str) -> str:
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("a link", text)
    text = _HEADING.sub("", text)
    text = _LIST_MARK.sub("", text)
    text = re.sub(r"[*_`|>]+", "", text)
    return " ".join(text.split())


class SentenceSplitter:
    """Feed streamed text; get back complete sentences to speak. Code blocks are skipped."""

    def __init__(self) -> None:
        self.buf = ""
        self.in_code = False

    def feed(self, text: str) -> list[str]:
        self.buf += text
        out: list[str] = []
        while True:
            if self.in_code:
                k = self.buf.find("```")
                if k == -1:
                    self.buf = self.buf[-2:]
                    break
                self.buf = self.buf[k + 3:]
                self.in_code = False
                continue
            k = self.buf.find("```")
            m = _SENTENCE_END.search(self.buf)
            if k != -1 and (m is None or k < m.start()):
                out.append(self.buf[:k])
                self.buf = self.buf[k + 3:]
                self.in_code = True
                continue
            if m is None:
                break
            out.append(self.buf[:m.end()])
            self.buf = self.buf[m.end():]
        return [s for s in (clean_for_speech(x) for x in out) if s]

    def flush(self) -> list[str]:
        rest, self.buf = self.buf, ""
        if self.in_code:
            self.in_code = False
            return []
        s = clean_for_speech(rest)
        return [s] if s else []
