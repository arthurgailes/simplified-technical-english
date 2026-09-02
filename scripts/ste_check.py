#!/usr/bin/env python
"""Check text against the ASD-STE100 writing rules.

The point of this script is that STE compliance is checkable rather than
asserted. It reads the dictionary extracted by build_dictionary.py, so every
vocabulary verdict traces back to the standard itself, and every finding cites
the rule it comes from.

It is a lint, not a grammar engine: it has no part-of-speech tagger, so a few
checks (multi-word nouns, part of speech) are reported as advisories that a
writer should confirm. The vocabulary and length checks are exact.

Usage:
    python scripts/ste_check.py FILE [FILE ...]
    python scripts/ste_check.py --mode procedural FILE
    python scripts/ste_check.py --domain-terms my_glossary.txt FILE
    python scripts/ste_check.py --json FILE
    cat draft.md | python scripts/ste_check.py -
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
DICT_PATH = ROOT / "assets" / "ste_dictionary.json"
DEFAULT_DOMAIN_TERMS = ROOT / "assets" / "domain_terms.txt"

MAX_WORDS_PROCEDURAL = 20   # rule 5.1
MAX_WORDS_DESCRIPTIVE = 25  # rule 6.3
MAX_SENTENCES_PARAGRAPH = 6  # rule 6.6
MAX_MULTI_WORD_NOUN = 3     # rule 2.1

# Words that carry structure rather than content. They are all approved, but the
# checks below need to know which words cannot be part of a noun cluster.
FUNCTION_WORDS = {
    "a", "an", "the", "this", "that", "these", "those", "and", "or", "but",
    "if", "when", "then", "as", "of", "in", "on", "at", "to", "for", "from",
    "with", "without", "by", "into", "onto", "out", "up", "down", "over",
    "under", "above", "below", "before", "after", "during", "between",
    "through", "thru", "not", "no", "all", "each", "more", "less", "than",
    "is", "are", "was", "were", "be", "been", "will", "can", "must", "do",
    "does", "did", "you", "it", "its", "they", "them", "their", "we", "us",
    "there", "here", "also", "only", "because", "so", "same", "other",
}

CONTRACTIONS = re.compile(
    r"\b(?:can't|won't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't"
    r"|it's|that's|there's|you're|we're|they're|i'm|let's|hasn't|haven't"
    r"|hadn't|shouldn't|couldn't|wouldn't|mustn't)\b", re.I)

BRITISH_SPELLINGS = {
    "colour": "color", "behaviour": "behavior", "labour": "labor",
    "neighbourhood": "neighborhood", "centre": "center", "metre": "meter",
    "litre": "liter", "fibre": "fiber", "analyse": "analyze",
    "organise": "organize", "organisation": "organization",
    "recognise": "recognize", "utilise": "utilize", "authorise": "authorize",
    "programme": "program", "catalogue": "catalog", "defence": "defense",
    "licence": "license", "practise": "practice", "travelled": "traveled",
    "cancelled": "canceled", "modelling": "modeling", "labelled": "labeled",
    "aluminium": "aluminum", "grey": "gray", "storey": "story",
    "judgement": "judgment", "acknowledgement": "acknowledgment",
}

SAFETY_WORDS = ("warning", "caution", "danger", "attention", "notice")

# The parts of speech a word can hold and still belong inside a multi-word noun.
NOUN_LIKE = {"n", "adj"}
# The parts of speech that cannot: English coins no new ones, so reading these
# off the dictionary's labels gives a complete list where a hand-written one
# would not be.
CLOSED_CLASS_POS = {"prep", "conj", "pron", "art"}

# The message rule 1.6 gives a word the dictionary does not carry. It is a
# constant because the report groups on it: defining the text in one place and
# matching it in another is two definitions of one fact.
NOT_IN_DICTIONARY = "is not in the dictionary"

# The suffixes an English verb inflects with, and what each leaves of the stem.
# Only these are stripped. Taking three letters off any word at all turns
# "runway" into RUN, which breaks a legitimate noun cluster.
GERUND_SUFFIXES = (("ing", ""), ("ing", "e"), ("ying", "ie"))
VERB_SUFFIXES = (("ies", "y"), ("es", ""), ("es", "e"), ("s", ""),
                 ("ed", ""), ("ed", "e"), ("d", "")) + GERUND_SUFFIXES

MIN_STEM_LETTERS = 3    # below this a "stem" is an accident of spelling
MIN_GERUND_LETTERS = 6  # "-ing" plus a stem worth the name


def undouble(stem: str) -> str | None:
    """The stem behind a doubled final consonant: "stopp" -> "stop"."""
    if len(stem) > MIN_STEM_LETTERS and stem[-1] == stem[-2]:
        return stem[:-1]
    return None


def stem_candidates(word: str, suffixes: tuple[tuple[str, str], ...]) -> list[str]:
    """The stems a word could inflect from, longest suffix first."""
    candidates: list[str] = []
    for suffix, ending in suffixes:
        if not word.endswith(suffix) or len(word) <= len(suffix) + 1:
            continue
        stem = word[:-len(suffix)] + ending
        for candidate in (stem, undouble(stem)):
            if candidate and len(candidate) >= MIN_STEM_LETTERS                     and candidate not in candidates:
                candidates.append(candidate)
    return candidates

# Anything inside these is not prose and is not checked.
CODE_FENCE = re.compile(r"^\s*```")
INLINE_CODE = re.compile(r"`[^`]*`")
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
URL = re.compile(r"https?://\S+|www\.\S+")

# A markdown table is columns, not prose. Its cells belong to different columns,
# so the pipe-to-space rule in prose_lines would join a whole row into one
# run-on "sentence" and report phantom length (5.1/6.3), noun-cluster (2.1) and
# paragraph (6.6) findings. Each cell is checked on its own instead, and the
# separator row (---|:--:) carries no prose at all.
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
# A vertical-list item. Rule 8.4 makes a colon a sentence end only inside one;
# in ordinary prose a colon is punctuation and must not split a sentence.
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)]|[A-Z][.)])\s+")


@dataclass
class Finding:
    line: int
    col: int
    rule: str
    message: str
    suggestion: str = ""
    # A violation is something the standard settles on its own: the dictionary
    # marks the word unapproved, the sentence is over the word limit, the
    # semicolon is banned. An advisory needs a judgment the script cannot make
    # without a part-of-speech tagger -- whether a word is a technical noun of
    # the subject field, whether an "-ing" form is a gerund, whether a passive
    # is the permitted kind. Mixing the two would bury the certain findings
    # under the uncertain ones.
    severity: str = "violation"
    term: str = ""

    def format(self, path: str) -> str:
        tail = f" -- use: {self.suggestion}" if self.suggestion else ""
        return f"{path}:{self.line}:{self.col} - RULE {self.rule} - {self.message}{tail}"


# --- Dictionary access --------------------------------------------------------


class Dictionary:
    def __init__(self, data: dict, domain_terms: set[str]):
        self.approved_forms: dict[str, list[str]] = data["approved_forms"]
        self.entries: list[dict] = data["entries"]

        # A domain term written in lower case matches any capitalization, which
        # is what an ordinary technical noun wants: "mortgage" and "Mortgage"
        # are the same word. A term written with a capital in it matches only
        # that spelling. That is what makes an identifier admissible without
        # opening a hole: rule 8.6 counts PASS and FAIL as identifiers, and
        # admitting the token must not also admit "pass" and "fail", which the
        # dictionary disallows as verbs.
        self.cased_terms = {t for t in domain_terms if not t.islower()}
        self.domain_terms = {t.lower() for t in domain_terms if t.islower()}
        self.all_terms = self.domain_terms | {t.lower() for t in self.cased_terms}

        self.unapproved: dict[str, list[dict]] = {}
        self.approved_headwords: set[str] = set()
        for e in self.entries:
            key = e["headword"].lower()
            if e["approved"]:
                self.approved_headwords.add(key)
            else:
                self.unapproved.setdefault(key, []).append(e)

        # Multi-word entries such as "carry out" or "have to" cannot be caught
        # token by token, so they are matched as phrases (rules 9.3 and 1.1).
        self.unapproved_phrases = sorted(
            (k for k in self.unapproved if " " in k),
            key=lambda k: -len(k.split()),
        )
        self.domain_phrases = sorted(
            (t.lower() for t in domain_terms if " " in t),
            key=lambda t: -len(t.split()),
        )

        # A domain term can collide with a word the standard disapproves:
        # "permit" is a building permit here and LET (v) in the dictionary,
        # "spread" is a Treasury spread here and APPLY (v) there. Admitting the
        # term must not silence the dictionary, or every domain added would
        # quietly punch another hole in the vocabulary check -- and the union of
        # every domain's jargon approaches ordinary English. So collisions are
        # recorded and reported as advisories instead of disappearing.
        self.collisions: dict[str, dict] = {}
        for term in self.domain_terms:
            entries = self.unapproved.get(term)
            if not entries:
                continue
            alternatives: list[str] = []
            for e in entries:
                alternatives += [a["word"] for a in e["alternatives"]]
            self.collisions[term] = {
                "disapproved_as": sorted({e["pos"] for e in entries if e["pos"]}),
                "approved_as": self.approved_forms.get(term, []),
                "alternatives": sorted(set(alternatives)),
            }

        # Past participles come from the dictionary's own inflection lists, so
        # the passive-voice check needs no hand-written irregular verb table.
        self.participles: set[str] = set()
        for e in self.entries:
            if e["approved"] and e["pos"] == "v" and e["forms"]:
                self.participles.add(e["forms"][-1].lower())

        self._index_parts_of_speech()

    def _index_parts_of_speech(self) -> None:
        """Build the three views of the dictionary the rule checks need."""
        # Every part of speech the standard gives each word, keyed by the word
        # and by each of its inflections. Two checks need it, and taking it
        # from the dictionary keeps them out of the business of maintaining
        # their own word lists.
        self.pos_by_word: dict[str, set[str]] = {}
        for e in self.entries:
            self.pos_by_word.setdefault(e["headword"].lower(), set()).add(e["pos"])
            for form in e["forms"] or []:
                self.pos_by_word.setdefault(form.lower(), set()).add(e["pos"])
        # Closed-class words, from the dictionary's own labels. A preposition,
        # conjunction, pronoun or article cannot be part of a multi-word noun,
        # and English does not coin new ones, so this list is complete in a way
        # a hand-written one never is. An adverb counts only when the word is
        # nothing else: "much", "off" and "last" are adverbs and adjectives
        # both, and an adjective does belong in a noun cluster.
        self.closed_class: set[str] = {
            word for word, pos in self.pos_by_word.items()
            if pos & CLOSED_CLASS_POS or pos == {"adv"}
        }
        # Every verb the standard names, approved or not, with its inflections.
        # Rule 3.5 restricts the "-ing" form of a verb, so deciding whether a
        # word is one is a question about verbs, and the dictionary can answer
        # it. Unapproved entries count: "spend" is disallowed, but "spending"
        # is still its gerund.
        self.verbs: set[str] = {word for word, pos in self.pos_by_word.items()
                                if "v" in pos}

    def is_approved(self, token: str) -> bool:
        return token.lower() in self.approved_forms

    def is_domain(self, token: str) -> bool:
        return token in self.cased_terms or token.lower() in self.domain_terms

    def approved_as(self, token: str) -> list[str]:
        """The parts of speech the dictionary approves this word as."""
        return self.approved_forms.get(token.lower(), [])

    # The suffixes an English verb inflects with, and what the stem gets back.
    # Only these are stripped: taking three letters off any word at all turns
    # "runway" into RUN and breaks a legitimate noun cluster.
    VERB_SUFFIXES = (("ies", "y"), ("es", ""), ("es", "e"), ("s", ""),
                     ("ed", ""), ("ed", "e"), ("d", ""),
                     ("ing", ""), ("ing", "e"))

    def verb_stem(self, token: str) -> str | None:
        """The verb this word inflects, or None if it does not look like one."""
        low = token.lower()
        if low in self.verbs:
            return low
        candidates: list[str] = []
        for suffix, ending in self.VERB_SUFFIXES:
            if low.endswith(suffix) and len(low) > len(suffix) + 1:
                stem = low[:-len(suffix)] + ending
                candidates.append(stem)
                if len(stem) > 2 and stem[-1] == stem[-2]:
                    candidates.append(stem[:-1])   # stopped -> stop
        for candidate in candidates:
            if len(candidate) > 2 and candidate in self.verbs \
                    and not self.pos_by_word.get(candidate, set()) & NOUN_LIKE:
                return candidate
        return None

    def breaks_noun_cluster(self, token: str) -> bool:
        """True when a word cannot be part of a multi-word noun (rule 2.1).

        The check that uses this walks a run of content words and reports a run
        longer than three. Without a way to stop at a verb or a preposition the
        run swallows whole clauses and reports them as noun clusters, which is
        worse than reporting nothing: "measurement crosses earlier policies" is
        a sentence, not a four-word noun.
        """
        low = token.lower()
        if self.is_domain(token):
            return False
        if low in FUNCTION_WORDS or low in self.closed_class:
            return True
        # Where the standard approves the word, its approved part of speech
        # settles the question, because that is the part of speech the text is
        # supposed to be using it as. MIX is approved as a verb and disallowed
        # as a noun -- rule 1.2 says use MIXTURE for the noun -- so in STE
        # "mix" is a verb and ends the cluster, even though the dictionary
        # carries the disallowed noun sense too.
        approved = self.approved_as(low)
        if approved:
            if NOUN_LIKE.intersection(approved):
                return False
            return "v" in approved
        pos = self.pos_by_word.get(low, set())
        if pos & NOUN_LIKE:
            return False        # a noun or an adjective belongs in the cluster
        if "v" in pos:
            return True
        return self.verb_stem(low) is not None

    def gerund_of(self, token: str) -> str | None:
        """The verb this word is the "-ing" form of, or None if it is not one.

        Rule 3.5 is about the "-ing" form of a verb, so the test is whether
        removing the suffix leaves one. "Turning" leaves TURN and "spending"
        leaves SPEND, but "ceiling" leaves "ceil" and "nothing" leaves "noth",
        which are not verbs -- those words end in the same three letters
        without being gerunds at all. Asking the dictionary is what keeps this
        from becoming a hand-written list of exceptions that has to grow by one
        entry every time a new "-ing"-shaped noun turns up.
        """
        low = token.lower()
        if not low.endswith("ing") or len(low) < MIN_GERUND_LETTERS:
            return None
        for candidate in stem_candidates(low, GERUND_SUFFIXES):
            if candidate in self.verbs:
                return candidate
        return None

    def disapproved_pos(self, token: str) -> list[str]:
        return [e["pos"] for e in self.unapproved.get(token.lower(), []) if e["pos"]]

    def alternatives_for(self, token: str) -> list[str]:
        out: list[str] = []
        for e in self.unapproved.get(token.lower(), []):
            for alt in e["alternatives"]:
                label = f'{alt["word"]}' + (f' ({alt["pos"]})' if alt["pos"] else "")
                if label not in out:
                    out.append(label)
            if not e["alternatives"] and e.get("note"):
                out.append(e["note"])
        return out

    def approved_only_as(self, token: str) -> tuple[list[str], list[str]] | None:
        """For words the dictionary approves as one part of speech but not another."""
        key = token.lower()
        if key not in self.approved_forms or key not in self.unapproved:
            return None
        good = self.approved_forms[key]
        bad = [e["pos"] for e in self.unapproved[key] if e["pos"]]
        bad = [p for p in bad if p not in good]
        return (good, bad) if bad else None


def load_dictionary(domain_files: list[Path]) -> Dictionary:
    if not DICT_PATH.exists():
        sys.exit(f"missing {DICT_PATH}; run scripts/build_dictionary.py first")
    data = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    terms: set[str] = set()
    for path in domain_files:
        if not path.exists():
            sys.exit(f"domain terms file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            # Case is kept: an all-lower-case term matches any capitalization,
            # a term with a capital in it matches only that spelling.
            line = line.split("#")[0].strip()
            if line:
                terms.add(line)
    return Dictionary(data, terms)


# --- Text preparation ---------------------------------------------------------


BLOCK_START = re.compile(r"^\s*(#{1,6}|[-*+]|\d+[.)]|[A-Z][.)])\s+")


class Line(NamedTuple):
    number: int
    text: str
    starts_block: bool


def prose_lines(text: str) -> list[Line]:
    """Return prose lines, with code and links removed.

    `starts_block` marks a line that opens a heading or an item of a vertical
    list. Rule 4.3 recommends a vertical list as the alternative to a long
    paragraph, so a list is not one paragraph and its items must not be counted
    together against rule 6.6.
    """
    out: list[Line] = []
    in_fence = False
    for i, raw in enumerate(text.splitlines(), start=1):
        if CODE_FENCE.match(raw):
            in_fence = not in_fence
            out.append(Line(i, "", False))
            continue
        if in_fence or raw.lstrip().startswith(("    ", "\t")) and not raw.strip():
            out.append(Line(i, "", False))
            continue

        stripped = raw.strip()
        # A table separator row (---|:--:) carries no prose.
        if "|" in stripped and "-" in stripped and set(stripped) <= set(" :|-"):
            out.append(Line(i, "", False))
            continue
        # A table content row: split it into cells so the length and noun-cluster
        # checks stay inside a cell, and mark it as a block so rule 6.6 does not
        # count a whole table as one paragraph.
        if TABLE_ROW.match(raw) and stripped.count("|") >= 2:
            line = INLINE_CODE.sub(" ", raw)
            line = MD_LINK.sub(r"\1", line)
            line = URL.sub(" ", line)
            cells = [c.strip().replace(":", " ")
                     for c in line.strip().strip("|").split("|")]
            line = ". ".join(c for c in cells if c)
            out.append(Line(i, line + "." if line else "", True))
            continue

        starts_block = bool(BLOCK_START.match(raw))
        is_list_item = bool(LIST_ITEM.match(raw))
        line = INLINE_CODE.sub(" ", raw)
        line = MD_LINK.sub(r"\1", line)
        line = URL.sub(" ", line)
        line = BLOCK_START.sub(lambda m: " " * len(m.group(0)), line)
        line = re.sub(r"[*_>|]", " ", line)
        # Rule 8.4 makes a colon a sentence end only in a vertical list. Outside
        # one it is punctuation, so keeping it as a terminator would split a
        # descriptive sentence into fragments and inflate the rule 6.6 count.
        if not is_list_item:
            line = line.replace(":", " ")
        out.append(Line(i, line, starts_block))
    return out


@dataclass
class Sentence:
    line: int
    col: int
    text: str
    paragraph: int


def split_sentences(lines: list[Line]) -> list[Sentence]:
    """Split into sentences, tracking where each one starts in the file.

    Rule 8.4: in a vertical list a colon ends a sentence, so it is a terminator
    alongside . ! and ? -- prose_lines has already removed colons from every
    line that is not a list item, so a colon reaches here only from a list.

    A heading or a list item starts a new paragraph even without a blank line
    before it, so that rule 6.6 counts each item of a vertical list on its own.
    """
    sentences: list[Sentence] = []
    paragraph = 0
    buf: list[tuple[int, int, str]] = []
    blank_run = True

    def flush() -> None:
        if not buf:
            return
        text = " ".join(part for _, _, part in buf).strip()
        if text:
            sentences.append(Sentence(buf[0][0], buf[0][1] + 1, text, paragraph))
        buf.clear()

    for lineno, line, starts_block in lines:
        if not line.strip():
            flush()
            if not blank_run:
                paragraph += 1
            blank_run = True
            continue
        if starts_block and not blank_run:
            flush()
            paragraph += 1
        blank_run = False
        pos = 0
        for match in re.finditer(r"[.!?:](?=\s|$)", line):
            end = match.end()
            chunk = line[pos:end]
            if chunk.strip():
                buf.append((lineno, pos + len(chunk) - len(chunk.lstrip()), chunk))
            # An abbreviation such as "No." is not a sentence end.
            if re.search(r"\b(?:No|Fig|Ref|approx|e\.g|i\.e|vs|Dr|Mr|Ms|St)\.$",
                         chunk.strip()):
                continue
            flush()
            pos = end
        rest = line[pos:]
        if rest.strip():
            buf.append((lineno, pos + len(rest) - len(rest.lstrip()), rest))
    flush()
    return sentences


def ste_word_count(sentence: str) -> int:
    """Count words the way section 8 of the standard counts them.

    Rules 8.5 thru 8.7: parenthetical text, quoted text, numbers with their
    units, abbreviations, alphanumeric identifiers, and hyphenated words each
    count as one word. Counting them any other way would flag correct STE
    sentences as too long.
    """
    text = re.sub(r"\([^)]*\)", " ONEWORD ", sentence)
    text = re.sub(r'"[^"]*"', " ONEWORD ", text)
    # A number and the unit that follows it are one word.
    text = re.sub(r"\b\d[\d,.]*\s*[A-Za-z%$/]*\b", " ONEWORD ", text)
    tokens = re.findall(r"[A-Za-z][A-Za-z'’-]*|ONEWORD", text)
    return len(tokens)


def tokenize(sentence: str) -> list[tuple[str, int]]:
    """Words with their offsets, ignoring anything inside parentheses or quotes."""
    masked = re.sub(r"\([^)]*\)", lambda m: " " * len(m.group(0)), sentence)
    masked = re.sub(r'"[^"]*"', lambda m: " " * len(m.group(0)), masked)
    return [(m.group(0), m.start())
            for m in re.finditer(r"[A-Za-z][A-Za-z'’-]*", masked)]


# --- Checks -------------------------------------------------------------------


def looks_procedural(sentence: str, d: Dictionary) -> bool:
    """An instruction starts with a command verb (rule 5.3)."""
    tokens = tokenize(sentence)
    if not tokens:
        return False
    first = tokens[0][0].lower()
    if first in {"do", "make", "put", "install", "remove", "refer", "obey"}:
        return True
    if first in {"if", "when", "before", "after"}:
        # A condition can precede the command (rule 5.4).
        for word, _ in tokens[1:]:
            if word == ",":
                break
        after_comma = sentence.split(",", 1)
        if len(after_comma) == 2:
            return looks_procedural(after_comma[1], d)
        return False
    pos = d.approved_forms.get(first, [])
    return "v" in pos and first not in FUNCTION_WORDS


DETERMINERS = {
    "the", "a", "an", "this", "that", "these", "those", "its", "their", "your",
    "each", "all", "no", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "of", "in", "on", "at", "for", "with", "from",
    "to", "into", "and", "or", "between", "through", "thru",
}


def likely_noun_usage(tokens: list[tuple[str, int]], index: int,
                      d: "Dictionary") -> bool:
    """Rough test for "this word is being used as a noun here".

    The dictionary disapproves many words for one part of speech while the same
    spelling is a perfectly good technical noun -- "fuel", "clamp", "switch",
    "hoist". Without this test the checker reports every one of those as a
    violation in text that is in fact correct STE. A determiner or preposition
    before the word, or a plural ending, is weak evidence but it is the
    evidence available, so the result is reported as an advisory either way.
    """
    if index == 0:
        return False
    # Only an immediately preceding determiner counts. Wider evidence (a plural
    # ending, a nearby verb) was tried and it swallowed the findings that matter
    # most -- "ensure", "however", "verify" all sit right after a plural noun or
    # an approved verb, and demoting those defeats the purpose of the check.
    return tokens[index - 1][0].lower() in DETERMINERS


def check_vocabulary(s: Sentence, d: Dictionary, findings: list[Finding]) -> None:
    lowered = s.text.lower()
    covered: set[int] = set()

    # Phrases first, so "carry out" is reported once rather than twice.
    for phrase in d.domain_phrases:
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", lowered):
            covered.update(range(m.start(), m.end()))
            collision = d.collisions.get(phrase)
            if collision:
                findings.append(Finding(
                    s.line, s.col + m.start(), "1.6",
                    f'"{phrase}" is admitted as a technical noun, but the '
                    f"dictionary disallows it",
                    "confirm it is the technical noun here; otherwise use "
                    + ", ".join(collision["alternatives"][:3]),
                    severity="advisory", term=phrase,
                ))
    for phrase in d.unapproved_phrases:
        for m in re.finditer(rf"\b{re.escape(phrase)}\b", lowered):
            if any(i in covered for i in range(m.start(), m.end())):
                continue
            covered.update(range(m.start(), m.end()))
            alts = d.alternatives_for(phrase)
            rule = "9.3" if len(phrase.split()) == 2 and phrase.split()[1] in {
                "out", "up", "off", "down", "over", "away", "back", "on", "in"
            } else "1.1"
            findings.append(Finding(
                s.line, s.col + m.start(), rule,
                f'"{phrase}" is not approved',
                ", ".join(alts[:4]),
            ))

    tokens = tokenize(s.text)
    for index, (word, offset) in enumerate(tokens):
        if offset in covered:
            continue
        key = word.lower().strip("'’-")
        if not key or len(key) == 1:
            continue
        if "'" in key or "’" in key:
            continue  # already reported as a contraction (rule 4.2)
        if key in SAFETY_WORDS:
            continue  # a safety label, checked by rules 7.1 thru 7.3
        if key in BRITISH_SPELLINGS:
            findings.append(Finding(
                s.line, s.col + offset, "1.14",
                f'"{word}" is British spelling',
                BRITISH_SPELLINGS[key], term=key,
            ))
            continue
        if d.is_domain(word.strip("'’-")):
            collision = d.collisions.get(key)
            if collision:
                roles = "/".join(collision["disapproved_as"]) or "this sense"
                approved = f'; approved as {"/".join(collision["approved_as"])}' \
                    if collision["approved_as"] else ""
                findings.append(Finding(
                    s.line, s.col + offset, "1.6",
                    f'"{word}" is admitted as a technical noun, but the '
                    f"dictionary disallows it as {roles}{approved}",
                    "confirm it is the technical noun here; as "
                    f'{roles} use {", ".join(collision["alternatives"][:3])}'
                    if collision["alternatives"] else "confirm it is the technical noun here",
                    severity="advisory", term=key,
                ))
            continue
        if d.is_approved(key):
            mismatch = d.approved_only_as(key)
            if mismatch:
                good, bad = mismatch
                findings.append(Finding(
                    s.line, s.col + offset, "1.2",
                    f'"{word}" is approved only as {"/".join(good)}, '
                    f'not as {"/".join(bad)}',
                    ", ".join(d.alternatives_for(key)[:3]),
                    severity="advisory", term=key,
                ))
            continue
        # A capitalized word inside a sentence is a proper noun (rule 8.6).
        # All-caps is not evidence of that: the standard prints its own STE
        # examples in capitals.
        if word[0].isupper() and not word.isupper() and offset > 0:
            continue

        alts = d.alternatives_for(key)
        if not alts:
            findings.append(Finding(
                s.line, s.col + offset, "1.6",
                f'"{word}" {NOT_IN_DICTIONARY}',
                "keep it only if it is a technical noun of the subject field, "
                "then add it to the domain terms file",
                severity="advisory", term=key,
            ))
            continue

        bad_pos = d.disapproved_pos(key)
        # The dictionary often disapproves a word for one part of speech while
        # approving the same spelling as a technical noun: "OIL (TN)" is listed
        # as an alternative for "oil (v)". Calling that a violation would be
        # wrong every time the word is used as the noun.
        self_alternative = any(
            a["word"].lower() == key
            for e in d.unapproved.get(key, []) for a in e["alternatives"])
        allowed_roles = {"v", "adj", "adv"} | ({"n"} if self_alternative else set())
        noun_ok = bad_pos and all(p in allowed_roles for p in bad_pos) \
            and (self_alternative or likely_noun_usage(tokens, index, d))
        if noun_ok:
            findings.append(Finding(
                s.line, s.col + offset, "1.6",
                f'"{word}" is not approved as {"/".join(bad_pos)}, and looks '
                f"like a noun here",
                "confirm it is a technical noun; if it is a verb, use "
                + ", ".join(alts[:3]),
                severity="advisory", term=key,
            ))
        else:
            findings.append(Finding(
                s.line, s.col + offset, "1.1",
                f'"{word}" is not approved', ", ".join(alts[:4]), term=key))


def check_verbs(s: Sentence, d: Dictionary, findings: list[Finding],
                procedural: bool) -> None:
    text = s.text

    for m in re.finditer(r"\b(?:have|has|had)\s+(?:been\s+)?(\w+ed|\w+en)\b", text, re.I):
        # "has red" is not a perfect tense; only treat the word as a participle
        # if the dictionary says it is one.
        candidate = m.group(1).lower()
        if candidate not in d.participles and len(candidate) < 6:
            continue
        findings.append(Finding(s.line, s.col + m.start(), "3.2",
                                f'"{m.group(0)}" is a perfect tense',
                                "the simple past or simple present tense"))
    for m in re.finditer(r"\b(?:is|are|was|were|be|been|being)\s+(\w+ing)\b", text, re.I):
        # Rule 3.3 permits an approved adjective after "to be": "the leg is
        # missing" states a condition, it is not a progressive tense. MISSING
        # and REMAINING are both approved as adjectives, so flagging them here
        # would contradict the alternative the dictionary itself gives for
        # "absent". A technical noun after "to be" is a predicate too.
        follower = m.group(1).lower()
        if "adj" in d.approved_as(follower) or d.is_domain(follower):
            continue
        findings.append(Finding(s.line, s.col + m.start(), "3.2",
                                f'"{m.group(0)}" is a progressive tense',
                                "the simple present or simple past tense"))
    for m in re.finditer(r"\b(?:should|would|could|might|may|shall|ought)\b", text, re.I):
        findings.append(Finding(s.line, s.col + m.start(), "3.4",
                                f'"{m.group(0)}" is not an approved auxiliary verb',
                                "CAN, MUST, or WILL"))

    for m in re.finditer(r"\b(is|are|was|were|be|been)\s+(?:\w+ly\s+)?(\w+)\b", text, re.I):
        candidate = m.group(2).lower()
        if candidate in FUNCTION_WORDS:
            continue
        if candidate in d.participles or (candidate.endswith("ed") and len(candidate) > 4):
            after = text[m.end():m.end() + 6].lower()
            by_agent = after.lstrip().startswith("by")
            # Rule 3.3 makes a past participle after "to be" an adjective that
            # describes a condition, not passive voice: "the valve is closed"
            # is correct STE. What rule 3.6 forbids is a real passive, and the
            # only sign of one the script can trust is a named agent. Anything
            # else is left as an advisory rather than faking a distinction that
            # needs the meaning of the sentence.
            findings.append(Finding(
                s.line, s.col + m.start(), "3.6",
                f'"{m.group(0)}" is passive voice'
                + (" with a named agent" if by_agent
                   else "; confirm this describes a condition (rule 3.3) "
                        "rather than hiding who acts"),
                "the active voice: name the doer and use a command or simple tense",
                severity="violation" if by_agent else "advisory",
            ))

def check_gerunds(s: Sentence, d: Dictionary, findings: list[Finding]) -> None:
    """Rule 3.5: the "-ing" form of a verb is allowed only as a technical noun."""
    for word, offset in tokenize(s.text):
        low = word.lower()
        if not low.endswith("ing"):
            continue
        if d.is_domain(low) or d.is_approved(low) or low in SAFETY_WORDS:
            continue
        # A word that ends in "-ing" without being a verb's gerund gets no
        # finding here. If the dictionary does not carry it, rule 1.6 already
        # asks whether it is a technical noun of the subject field -- the same
        # judgment this advisory asks for, so raising both would be two
        # advisories for one decision.
        stem = d.gerund_of(low)
        if stem:
            findings.append(Finding(
                s.line, s.col + offset, "3.5",
                f'"{word}" is the "-ing" form of {stem.upper()}',
                "permitted only as a technical noun or as a modifier in one",
                severity="advisory", term=low))


def check_sentence_shape(s: Sentence, d: Dictionary, procedural: bool,
                         findings: list[Finding]) -> None:
    limit = MAX_WORDS_PROCEDURAL if procedural else MAX_WORDS_DESCRIPTIVE
    rule = "5.1" if procedural else "6.3"
    count = ste_word_count(s.text)
    if count > limit:
        findings.append(Finding(
            s.line, s.col, rule,
            f"sentence is {count} words; the limit is {limit} for "
            f'{"procedural" if procedural else "descriptive"} text',
            "split it into two sentences",
        ))

    if procedural:
        parts = re.split(r"\band\b", s.text, flags=re.I)
        if len(parts) > 1:
            commands = sum(1 for p in parts if looks_procedural(p.strip(), d))
            if commands > 1:
                findings.append(Finding(
                    s.line, s.col, "5.2",
                    "this sentence contains more than one instruction",
                    "one instruction per sentence, unless the actions happen together",
                    severity="advisory",
                ))

    if ";" in s.text:
        findings.append(Finding(
            s.line, s.col + s.text.index(";"), "8.1",
            "the semicolon is not permitted",
            "a period, or a vertical list"))

    m = CONTRACTIONS.search(s.text)
    if m:
        findings.append(Finding(
            s.line, s.col + m.start(), "4.2",
            f'"{m.group(0)}" is a contraction', "the full form"))

    # Rule 2.1: a long run of content words is usually a long multi-word noun.
    tokens = tokenize(s.text)
    run: list[str] = []
    run_start = 0
    prev_end = 0
    for word, offset in tokens + [("", -1)]:
        low = word.lower()
        # Punctuation ends a noun cluster; without this, "study period, however,
        # tracts" reads as one five-word noun.
        broken = offset > 0 and re.search(r"[,;:.()\"]", s.text[prev_end:offset])
        if broken and len(run) <= MAX_MULTI_WORD_NOUN:
            run = []
        content = bool(word) and not broken and not d.breaks_noun_cluster(word)
        if offset >= 0:
            prev_end = offset + len(word)
        if content:
            if not run:
                run_start = offset
            run.append(word)
            continue
        if len(run) > MAX_MULTI_WORD_NOUN:
            findings.append(Finding(
                s.line, s.col + run_start, "2.1",
                f'"{" ".join(run)}" may be a multi-word noun of {len(run)} words; '
                f"the limit is {MAX_MULTI_WORD_NOUN}",
                "use prepositions to break it up, for example "
                '"calibration of the resistance of the runway light connection"',
                severity="advisory",
            ))
        run = []


def check_paragraphs(sentences: list[Sentence], findings: list[Finding]) -> None:
    counts: dict[int, list[Sentence]] = {}
    for s in sentences:
        counts.setdefault(s.paragraph, []).append(s)
    for group in counts.values():
        if len(group) > MAX_SENTENCES_PARAGRAPH:
            first = group[0]
            findings.append(Finding(
                first.line, first.col, "6.6",
                f"this paragraph has {len(group)} sentences; the limit is "
                f"{MAX_SENTENCES_PARAGRAPH}",
                "split it into two paragraphs, one topic each"))


def check_safety(lines: list[Line], findings: list[Finding]) -> None:
    """Rules 7.1 thru 7.3: a safety instruction needs a command and a reason."""
    for idx, prose in enumerate(lines):
        stripped = prose.text.strip()
        low = stripped.lower()
        if not any(low.startswith(w) for w in SAFETY_WORDS):
            continue
        body = stripped.split(":", 1)[1].strip() if ":" in stripped else ""
        if not body:
            for following in lines[idx + 1: idx + 3]:
                if following.text.strip():
                    body = following.text.strip()
                    break
        if not body:
            findings.append(Finding(prose.number, 1, "7.2",
                                    "this safety instruction has no command or condition"))
            continue
        first = re.match(r"[A-Za-z]+", body)
        if first and first.group(0).lower() in {"the", "a", "an", "this", "it", "there"}:
            findings.append(Finding(
                prose.number, 1, "7.2",
                "a safety instruction must start with a command or a condition, "
                "not with a description"))
        if len(re.findall(r"[.!?]", body)) < 2 and " because " not in body.lower() \
                and " to prevent " not in body.lower() and " can cause " not in body.lower():
            findings.append(Finding(
                prose.number, 1, "7.3",
                "this safety instruction does not explain the risk or the result",
                "add a sentence that says what can happen"))


# --- Driver -------------------------------------------------------------------


def check_text(text: str, d: Dictionary, mode: str) -> tuple[list[Finding], dict]:
    lines = prose_lines(text)
    sentences = split_sentences(lines)
    findings: list[Finding] = []
    for s in sentences:
        procedural = mode == "procedural" or (
            mode == "auto" and looks_procedural(s.text, d))
        check_vocabulary(s, d, findings)
        check_verbs(s, d, findings, procedural)
        check_gerunds(s, d, findings)
        check_sentence_shape(s, d, procedural, findings)
    check_paragraphs(sentences, findings)
    check_safety(lines, findings)
    findings.sort(key=lambda f: (f.line, f.col, f.rule))

    counts = [ste_word_count(s.text) for s in sentences] or [0]
    total_tokens = sum(len(tokenize(s.text)) for s in sentences)
    violations = [f for f in findings if f.severity == "violation"]
    advisories = [f for f in findings if f.severity == "advisory"]
    unapproved = sum(1 for f in violations if f.rule == "1.1")
    stats = {
        "sentences": len(sentences),
        "mean_sentence_words": round(sum(counts) / len(counts), 1),
        "max_sentence_words": max(counts),
        "words": total_tokens,
        "violations": len(violations),
        "advisories": len(advisories),
        "unapproved_words": unapproved,
        "unapproved_rate": round(unapproved / total_tokens, 3) if total_tokens else 0.0,
        "violations_by_rule": {r: sum(1 for f in violations if f.rule == r)
                               for r in sorted({f.rule for f in violations})},
        "advisories_by_rule": {r: sum(1 for f in advisories if f.rule == r)
                               for r in sorted({f.rule for f in advisories})},
    }
    return findings, stats


def looks_like_terminology(word: str, uses: int) -> bool:
    """Whether an unknown word looks like this field's vocabulary.

    A guess from shape and frequency, not a lexicon: a word that repeats, is
    hyphenated, or carries a capital that is not just the start of a sentence
    is far more likely to be a technical noun than a lower-case word used once.
    """
    return (uses > 1 or "-" in word or "_" in word
            or any(c.isdigit() for c in word)
            or any(c.isupper() for c in word[1:]))


def print_unknown_words(unknown: list[tuple[tuple[str, str], list[Finding]]]) -> None:
    """Print the rule 1.6 unknown words as one scannable block.

    The decision to make about each of them is the same, and the suggestion is
    the same twenty words every time, so a line each buries the advisories that
    do say something specific. Splitting the block puts the likely glossary
    candidates where they get read.
    """
    likely: list[str] = []
    ordinary: list[str] = []
    for (_, term), group in unknown:
        quoted = re.search(r'"([^"]+)"', group[0].message)
        word = quoted.group(1) if quoted else term
        label = word + (f" ({len(group)}x)" if len(group) > 1 else "")
        bucket = likely if looks_like_terminology(word, len(group)) else ordinary
        bucket.append(label)

    print(f"  RULE 1.6 - {len(unknown)} words are not in the dictionary. Keep "
          "each one only if it is a")
    print("    technical noun of the subject field, then add it to the domain "
          "terms file.")
    print("    Use --all-advisories for a line and a location for each one.")
    for heading, words in (
            ("repeated, hyphenated or capitalized -- most likely your "
             "terminology", likely),
            ("used once, ordinary shape -- most likely just English the "
             "standard omits", ordinary)):
        if not words:
            continue
        print(f"    {heading}:")
        for chunk in textwrap.wrap(", ".join(sorted(words, key=str.lower)), width=70):
            print(f"      {chunk}")


def print_advisories(advisories: list[Finding], collapse_unknown_words: bool) -> None:
    """Advisories repeat heavily, so they are grouped by term.

    One line per distinct word is enough to decide "yes, that is our
    terminology" and move on. The rule 1.6 unknown words collapse further
    still -- see print_unknown_words.
    """
    grouped: dict[tuple[str, str], list[Finding]] = {}
    for finding in advisories:
        grouped.setdefault((finding.rule, finding.term or finding.message),
                           []).append(finding)

    unknown = [(key, group) for key, group in grouped.items()
               if key[0] == "1.6" and group[0].term
               and NOT_IN_DICTIONARY in group[0].message]
    if collapse_unknown_words:
        for key, _ in unknown:
            del grouped[key]

    for (rule, _), group in sorted(grouped.items()):
        head = group[0]
        where = (f"{len(group)}x, first at line {head.line}" if len(group) > 1
                 else f"line {head.line}")
        tail = f" -- {head.suggestion}" if head.suggestion else ""
        print(f"  RULE {rule} - {head.message} ({where}){tail}")

    if unknown and collapse_unknown_words:
        print_unknown_words(unknown)



def report(path: str, findings: list[Finding], stats: dict,
           quiet: bool = False, show_advisories: bool = True,
           all_advisories: bool = False) -> None:
    violations = [f for f in findings if f.severity == "violation"]
    advisories = [f for f in findings if f.severity == "advisory"]

    print(f"\n=== {path} ===")
    if not quiet:
        if violations:
            print("\nVIOLATIONS - the standard settles these:")
            for f in violations:
                print("  " + f.format(path))
        if advisories and show_advisories:
            print("\nADVISORIES - these need your judgment:")
            print_advisories(advisories, collapse_unknown_words=not all_advisories)

    print(f"\n{stats['violations']} violations, {stats['advisories']} advisories "
          f"in {stats['sentences']} sentences ({stats['words']} words)")
    print(f"sentence length: mean {stats['mean_sentence_words']}, "
          f"max {stats['max_sentence_words']}")
    print(f"unapproved words: {stats['unapproved_words']} "
          f"({stats['unapproved_rate']:.1%})")
    if stats["violations_by_rule"]:
        print("violations by rule: " + ", ".join(
            f"{r}={n}" for r, n in stats["violations_by_rule"].items()))
    if not violations:
        print("PASS - no rule violations")


def audit_domain_terms(d: Dictionary, as_json: bool = False) -> int:
    """Show which admitted terms shadow a word the dictionary disallows.

    Worth running whenever the terms file grows. A term here is not
    necessarily wrong -- "permit" really is the housing word -- but each one is
    a place where the checker now needs a human to confirm the sense, and a
    place where a genuine misuse would only appear as an advisory.
    """
    hard, soft = [], []
    for term, info in sorted(d.collisions.items()):
        (soft if info["approved_as"] else hard).append((term, info))

    if as_json:
        print(json.dumps({"hard": dict(hard), "soft": dict(soft),
                          "domain_terms": len(d.all_terms)}, indent=1))
        return 0

    print(f"{len(d.all_terms)} domain terms admitted under rules 1.5/1.6/1.8 "
          f"({len(d.cased_terms)} matched case-sensitively)")
    print(f"{len(d.collisions)} of them shadow a dictionary entry.\n")
    if hard:
        print("The dictionary knows these words ONLY as unapproved. Using one in "
              "any\nsense other than your technical noun is a rule 1.1 violation "
              "the checker\nwill report as an advisory rather than a violation:")
        for term, info in hard:
            roles = "/".join(info["disapproved_as"]) or "?"
            print(f"  {term:22s} not approved as {roles:8s} -> "
                  f"{', '.join(info['alternatives'][:3])}")
    if soft:
        print("\nThese are approved as one part of speech and disallowed as "
              "another, so\nadmitting them silences a rule 1.2 advisory:")
        for term, info in soft:
            print(f"  {term:22s} approved as {'/'.join(info['approved_as']):8s} "
                  f"not as {'/'.join(info['disapproved_as'])}")
    if not d.collisions:
        print("No collisions: every admitted term is absent from the dictionary.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", help='files to check, or - for stdin')
    ap.add_argument("--mode", choices=["auto", "procedural", "descriptive"],
                    default="auto",
                    help="which sentence-length limit applies (default: decide per sentence)")
    ap.add_argument("--domain-terms", type=Path, action="append", default=[],
                    help="extra technical nouns to admit under rule 1.6; repeatable")
    ap.add_argument("--no-default-terms", action="store_true",
                    help="do not load assets/domain_terms.txt")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--quiet", action="store_true", help="print the summary only")
    ap.add_argument("--all-advisories", action="store_true",
                    help="print one line for each word that is not in the "
                         "dictionary, instead of the collapsed word list")
    ap.add_argument("--violations-only", action="store_true",
                    help="hide advisories, which need a judgment the script cannot make")
    ap.add_argument("--audit-domain-terms", action="store_true",
                    help="list the domain terms that shadow a word the dictionary "
                         "disallows, then exit")
    args = ap.parse_args()

    term_files = list(args.domain_terms)
    if not args.no_default_terms and DEFAULT_DOMAIN_TERMS.exists():
        term_files.insert(0, DEFAULT_DOMAIN_TERMS)
    d = load_dictionary(term_files)

    if args.audit_domain_terms:
        return audit_domain_terms(d, args.json)
    if not args.files:
        ap.error("give at least one file, or - for stdin")

    results = {}
    exit_code = 0
    for name in args.files:
        text = sys.stdin.read() if name == "-" else Path(name).read_text(encoding="utf-8")
        findings, stats = check_text(text, d, args.mode)
        results[name] = {"findings": [asdict(f) for f in findings], "stats": stats}
        if stats["violations"]:
            exit_code = 1
        if args.json:
            continue
        report(name, findings, stats, quiet=args.quiet,
               show_advisories=not args.violations_only,
               all_advisories=args.all_advisories)

    if args.json:
        print(json.dumps(results, indent=1))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
