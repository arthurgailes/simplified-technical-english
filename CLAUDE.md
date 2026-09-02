# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

A Claude Code skill package (`SKILL.md`) that teaches Claude to write and rewrite
text in ASD-STE100 Simplified Technical English. It bundles the standard's full
rules and dictionary, extracted from the source PDF, plus a Python compliance
checker. There is no application code here — the "product" is the skill
definition, the extracted reference data, and the checker script that verifies
output against that data.

## Commands

```bash
# Check a draft against the rules (from repo root)
python scripts/ste_check.py DRAFT.md
python scripts/ste_check.py --violations-only DRAFT.md
python scripts/ste_check.py --mode procedural STEPS.md   # forces the 20-word limit instead of 25
python scripts/ste_check.py --json DRAFT.md
python scripts/ste_check.py --domain-terms terms.txt DRAFT.md
python scripts/ste_check.py --no-default-terms --domain-terms terms.txt DRAFT.md
python scripts/ste_check.py --audit-domain-terms
python scripts/ste_check.py --all-advisories DRAFT.md
cat draft.md | python scripts/ste_check.py -

# Regression-test the checker itself (run after any change to ste_check.py or
# build_dictionary.py)
python scripts/selftest.py

# Regenerate all extracted reference data from the source PDF (only needed if
# a new issue of ASD-STE100 is published)
python scripts/build_dictionary.py
```

`build_dictionary.py` requires PyMuPDF (`import fitz`), which is not declared
in any manifest in this repo (there is no `pyproject.toml`/`requirements.txt`)
— install it ad hoc (`pip install pymupdf`) if you need to run it.
`ste_check.py` and `selftest.py` use only the standard library.

## Architecture

**Data flow is one-directional and generated, not hand-authored:**

```
references/ASD-STE100_ISSUE9.pdf
        │  scripts/build_dictionary.py
        ▼
assets/ste_dictionary.json  (2,198 entries: approved_forms index + full entries)
references/dictionary.md    (same data, greppable)
references/rules/section-*.md, general-recommendations.md
```

Never hand-edit the generated files above — edit `build_dictionary.py`'s
extraction logic and re-run it, or the next rebuild silently reverts your
change. The PDF's dictionary pages are parsed by reading row/column geometry
from the horizontal rule segments between cells (see the extraction docstring
in `build_dictionary.py`), not by whitespace heuristics — this is what makes
re-extraction reliable when a new issue of the standard ships.

**`scripts/ste_check.py` (the checker) separates two kinds of finding:**
- **Violations** — settled by the standard itself (unapproved word, sentence
  over the word limit, semicolon, contraction, perfect/progressive tense,
  passive with a named agent). These are exact, not heuristic.
- **Advisories** — need judgment a part-of-speech-free script can't make
  (is an unknown word a technical noun? is an "-ing" word a gerund-as-noun or
  a verb?). Gerund detection works by stripping known verb suffixes and
  looking the stem up in the dictionary's verb list (see `GERUND_SUFFIXES`/
  `VERB_SUFFIXES` in `ste_check.py`), not a hardcoded list.

`assets/domain_terms.txt` is the vocabulary escape hatch for subject fields
the aerospace-derived dictionary doesn't cover (rules 1.5/1.6/1.8). Terms
written in lower case match any capitalization; terms written with a capital
match only that spelling — this is how a term like `PASS` can be admitted
without silencing the disallowed verb "pass" elsewhere. `--audit-domain-terms`
lists every term that collides with a disallowed dictionary sense.

**`scripts/selftest.py`** validates the checker two ways: a corpus check
(rebuilds STE vs. non-STE example corpora straight from the standard's own
examples and asserts the checker separates them, thresholds `MAX_POSITIVE_RATE`
/ `MIN_NEGATIVE_RATE`), and a table of paired `Case`s where a rule must fire on
one text and stay silent on a near-identical one. When you change a rule's
detection logic, add a case pair — the corpus check moves by a fraction of a
percent per rule and won't catch a rule that got silenced while you were
chasing a false positive.

**`SKILL.md`** is the entry point Claude Code loads; it describes target
detection (named file vs. pasted text vs. in-progress document vs. ask), the
rewrite workflow, and what to deliver (rewrite + rule-grouped report + checker
verdict). Treat it as the spec for this skill's behavior — changes to checker
behavior should stay consistent with what `SKILL.md` promises.

## Copyright constraint

ASD-STE100 is copyright ASD. The PDF and everything extracted from it
(`references/`, `assets/ste_dictionary.json`) must never be published or
redistributed. The tooling (scripts, `SKILL.md`, `CLAUDE.md`, domain terms) is
publishable; the ASD-derived data is not.

## Two repositories

This working copy tracks the **private** repo (`origin`), which holds everything
including the copyright ASD data. The **public** repo ships only the tooling and
tells users to bring their own copy of the standard.

Publish to both with one command:

```bash
scripts/publish.sh "commit message"
```

It commits and pushes to the private repo, then mirrors an explicit whitelist
(scripts, `SKILL.md`, `CLAUDE.md`, domain terms, evals) into a local clone of
the public repo under `.public-mirror/` and pushes that. The ASD data is never
copied, and the public repo's own README (written in STE) and `.gitignore` are
left untouched.
