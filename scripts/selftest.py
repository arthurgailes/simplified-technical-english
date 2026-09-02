#!/usr/bin/env python
"""Check the checker.

A lint that cries wolf is worse than no lint: if ste_check.py reports violations
in text that the standard itself prints as correct STE, nobody will trust its
output on real drafts. So this builds two corpora straight from the extracted
dictionary -- the standard's own STE examples, and its own non-STE examples --
and asserts that the checker separates them.

Run it after any change to ste_check.py or build_dictionary.py:

    python scripts/selftest.py
"""

from __future__ import annotations

import contextlib
import io
import json
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ste_check import (DICT_PATH, DEFAULT_DOMAIN_TERMS, Dictionary, check_text,
                       load_dictionary, report)

SAMPLE_SIZE = 400
SEED = 11

# The standard's examples are aerospace text, so they are full of technical
# nouns ("pump", "clamp", "switch") that the dictionary disapproves as verbs and
# that no domain terms file here covers. A few of those are expected to surface;
# the point of the threshold is that they stay rare.
MAX_POSITIVE_RATE = 0.015
MIN_NEGATIVE_RATE = 0.03


# --- Rule cases ---------------------------------------------------------------
#
# The corpus measurement above is a statistic: it says the checker still tells
# STE from non-STE overall. It moves by a fraction of a percent for most single
# rule changes, so on its own it cannot tell you that a rule still fires where
# it should and stays quiet where it should not. These cases do that, one rule
# at a time, and each pairs a case that must be reported with one that must not.
# A rule is easy to silence by accident while chasing a false positive; the pair
# is what catches it.


@dataclass
class Case:
    name: str
    text: str
    must: tuple[str, ...] = ()          # rules that have to be reported
    must_not: tuple[str, ...] = ()      # rules that have to stay quiet
    terms: tuple[str, ...] = field(default_factory=tuple)


CASES = [
    # Rule 6.6 counts paragraphs. Rule 4.3 recommends a vertical list as the
    # alternative to one long paragraph, so the items of a list are not one
    # paragraph -- but an item that runs long is still over the limit.
    Case("6.6 counts a real paragraph",
         "One is here. Two is here. Three is here. Four is here. Five is here. "
         "Six is here. Seven is here.",
         must=("6.6",)),
    Case("6.6 does not count a list as one paragraph",
         "- One is here. Two is here. Three is here.\n"
         "- Four is here. Five is here. Six is here.\n"
         "- Seven is here. Eight is here. Nine is here.\n",
         must_not=("6.6",)),
    Case("6.6 still counts one long list item",
         "- One is here. Two is here. Three is here. Four is here. Five is "
         "here. Six is here. Seven is here.\n",
         must=("6.6",)),

    # Rule 3.2 bans progressive tenses; rule 3.3 permits an approved adjective
    # after "to be". MISSING and REMAINING are approved adjectives.
    Case("3.2 catches a progressive tense",
         "The pump is running.", must=("3.2",)),
    Case("3.2 leaves an approved adjective alone (rule 3.3)",
         "The leg is missing. The two bolts are remaining.",
         must_not=("3.2",)),

    # Rule 3.5 restricts the "-ing" form of a verb. A word that merely ends in
    # those letters is not one.
    Case("3.5 catches a gerund",
         "The turning of the bolt is easy.", must=("3.5",)),
    Case("3.5 ignores a word that is not a verb form",
         "Nothing touches the ceiling.", must_not=("3.5",)),

    # Rule 2.1 limits a multi-word noun to three words. A run that crosses a
    # verb or a preposition is a clause, not a noun cluster.
    Case("2.1 catches a long noun cluster",
         "The runway light connection calibration is complete.",
         must=("2.1",)),
    Case("2.1 does not read a clause as a noun cluster",
         "The measurement crosses earlier policies. "
         "The declined books against the proposal books are here.",
         must_not=("2.1",)),

    # A domain term in capitals is an identifier (rule 8.6) and admits only
    # that spelling, so it cannot quietly re-admit the disallowed ordinary word.
    Case("a capitalized domain term admits the identifier",
         "The result is a PASS.", must_not=("1.1",), terms=("PASS",)),
    Case("a capitalized domain term does not admit the ordinary word",
         "Do not pass the valve.", must=("1.1",), terms=("PASS",)),
    Case("a lower-case domain term matches any capitalization",
         "The mortgage is here. Mortgage rates increase.",
         must_not=("1.1", "1.6"), terms=("mortgage", "rates")),

    # A markdown table is columns, not prose. The cells of a row belong to
    # different columns, so the row must not read as one long sentence. Each
    # cell here is under the limit; joined across columns (the old behavior)
    # they are 27 words, so this fires 6.3 without the cell split.
    Case("a table row is checked cell by cell, not as one long sentence",
         "| This tests the market scale as an empirical question at several "
         "nested levels "
         "| This adds the national coverage and includes the non metro country "
         "the file drops |\n",
         must_not=("6.3",)),
    # The same, for rule 2.1: each cell is a three-word noun cluster (at the
    # limit), but joined across the column boundary they are a six-word run.
    Case("a table does not join cells into one noun cluster",
         "| affordable metro markets | county histogram basis |\n",
         must_not=("2.1",)),

    # Rule 8.4 makes a colon a sentence end only in a vertical list. In ordinary
    # prose the colon sentence stays whole, so a short paragraph does not trip
    # rule 6.6 on a phantom extra sentence (the colon-split gave seven).
    Case("a colon in prose does not start a new sentence",
         "Give each person three measures: their own area, the ring around it, "
         "and the rest of the region. One is here. Two is here. Three is here. "
         "Four is here. Five is here.\n",
         must_not=("6.6",)),
    # An invariant guard, not a red-for-this-change case: the colon must keep
    # ending a sentence inside a vertical list, so a list item whose halves are
    # each under the limit is not reported over length. It catches a future
    # change that drops list-colon handling, not the table/colon fix here.
    Case("a colon still ends a sentence in a vertical list",
         "- The first half of this list item runs to about fifteen words before "
         "the colon here: and the second half of the item also runs to about "
         "fifteen more words after it.\n",
         must_not=("6.3",)),
]


def build_dictionary(extra_terms: tuple[str, ...] = ()) -> Dictionary:
    data = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    terms: set[str] = set()
    for line in DEFAULT_DOMAIN_TERMS.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            terms.add(line)
    terms.update(extra_terms)
    return Dictionary(data, terms)


def run_cases() -> list[str]:
    failures: list[str] = []
    for case in CASES:
        d = build_dictionary(case.terms)
        findings, _ = check_text(case.text, d, "auto")
        seen = {f.rule for f in findings}
        for rule in case.must:
            if rule not in seen:
                failures.append(f"{case.name}: rule {rule} was not reported")
        for rule in case.must_not:
            if rule in seen:
                hit = next(f for f in findings if f.rule == rule)
                failures.append(
                    f"{case.name}: rule {rule} fired and should not have "
                    f"({hit.message})")
    return failures


def run_report_shape() -> list[str]:
    """The advisory block collapses by default and expands on request.

    Rule 1.6 fires on every ordinary word the standard has no entry for, which
    on a real document is hundreds of near-identical lines. Collapsing them is
    what keeps the specific advisories readable, so the collapse is behavior
    worth holding still.
    """
    d = build_dictionary()
    text = ("The pessimism of the aggregate proposal reconciles the hourly "
            "attribution. The stranded catalyst invalidates the decisive "
            "auditability of the segment.\n")
    findings, stats = check_text(text, d, "auto")

    def lines(**kwargs) -> int:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            report("t.md", findings, stats, **kwargs)
        return len(buf.getvalue().splitlines())

    failures: list[str] = []
    collapsed, expanded = lines(), lines(all_advisories=True)
    if collapsed >= expanded:
        failures.append(
            f"advisories did not collapse: default report is {collapsed} lines, "
            f"--all-advisories is {expanded}")
    if stats["advisories"] < 5:
        failures.append("the report-shape case stopped producing advisories; "
                        "it no longer tests anything")
    return failures


def corpora() -> tuple[list[str], list[str]]:
    data = json.loads(DICT_PATH.read_text(encoding="utf-8"))
    good: list[str] = []
    bad: list[str] = []
    for entry in data["entries"]:
        for alt in entry["alternatives"]:
            if alt["ste_example"]:
                good.append(alt["ste_example"])
            if alt["non_ste_example"]:
                bad.append(alt["non_ste_example"])
        for example in entry["examples"]:
            if example.startswith("(not STE)"):
                bad.append(example[len("(not STE)"):].strip())
            else:
                good.append(example)
    rng = random.Random(SEED)
    return (rng.sample(good, min(SAMPLE_SIZE, len(good))),
            rng.sample(bad, min(SAMPLE_SIZE, len(bad))))


def measure(sentences: list[str], d) -> tuple[float, dict]:
    findings, stats = check_text("\n\n".join(sentences), d, "auto")
    violations = [f for f in findings if f.severity == "violation"]
    rate = len(violations) / max(stats["words"], 1)
    return rate, stats


def main() -> int:
    d = load_dictionary([DEFAULT_DOMAIN_TERMS])
    good, bad = corpora()

    good_rate, good_stats = measure(good, d)
    bad_rate, bad_stats = measure(bad, d)

    print(f"STE examples     : {len(good)} sentences, {good_stats['words']} words, "
          f"{good_stats['violations']} violations ({good_rate:.2%} of words)")
    print(f"non-STE examples : {len(bad)} sentences, {bad_stats['words']} words, "
          f"{bad_stats['violations']} violations ({bad_rate:.2%} of words)")
    print(f"sentence length  : STE mean {good_stats['mean_sentence_words']}, "
          f"non-STE mean {bad_stats['mean_sentence_words']}")

    case_failures = run_cases() + run_report_shape()
    print(f"rule cases       : {len(CASES) + 1} cases, "
          f"{len(case_failures)} failed")

    failures = list(case_failures)
    if good_rate > MAX_POSITIVE_RATE:
        failures.append(
            f"false positives: {good_rate:.2%} of words in correct STE were "
            f"flagged, limit {MAX_POSITIVE_RATE:.2%}")
    if bad_rate < MIN_NEGATIVE_RATE:
        failures.append(
            f"weak detection: only {bad_rate:.2%} of words in non-STE text were "
            f"flagged, expected at least {MIN_NEGATIVE_RATE:.2%}")
    if bad_rate <= good_rate * 2:
        failures.append(
            f"no separation: non-STE {bad_rate:.2%} vs STE {good_rate:.2%}")
    if good_stats["max_sentence_words"] > 25:
        failures.append(
            f"word counting is wrong: an STE example measured "
            f"{good_stats['max_sentence_words']} words, over every limit "
            f"in the standard")

    for f in failures:
        print(f"FAIL  {f}")
    if not failures:
        print("PASS  every rule case holds and the checker separates STE from non-STE")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
