# Simplified Technical English checker (ASD-STE100)

This tool checks and rewrites text in ASD-STE100 Simplified Technical English (STE).
STE controls the words and the grammar of English. It is easy to read for persons
who do not speak English as their first language, and it is easy to translate.

This repository has two parts:

- A skill file (`SKILL.md`) that tells Claude Code how to write in STE.
- A Python checker (`scripts/ste_check.py`) that finds the words, sentences, and
  constructions that STE does not permit.

> **This README is written in STE.** It is an example of the result.

## You must supply the standard

The ASD-STE100 standard is copyright ASD. This repository does not include it.
The checker reads the dictionary and the rules from the standard, so you get your
own copy first. The copy is free.

### ▶ Get the standard here: **[asd-ste100.org](https://asd-ste100.org)**

You give some details on the official site. Then ASD sends you the PDF.

## Installation

1. Get the PDF from the link above.
2. Put the PDF at this path:
   `references/ASD-STE100_ISSUE9.pdf`
3. Start the setup script:
   `python scripts/init.py`

The setup script reads the PDF. Then it makes the dictionary and the rule files
that the checker needs. You do this one time.

If you do not have the PDF, start `python scripts/init.py`. The script shows you
the link and the steps again.

## How to use the checker

To check a file:

```bash
python scripts/ste_check.py DRAFT.md
```

To show only the certain problems:

```bash
python scripts/ste_check.py --violations-only DRAFT.md
```

The checker gives two kinds of result. A **violation** is a problem that the
standard settles on its own: an unapproved word, a sentence above the word limit,
or a semicolon. A **advisory** needs your judgment, because the script cannot see
if an unknown word is a technical noun of your subject. Correct the violations.
Read the advisories, and act where they are correct.

For all the options and the full method, read `SKILL.md`.

## What is necessary

- Python 3.
- PyMuPDF, for the setup script only. To install it: `pip install pymupdf`.
- The checker (`ste_check.py`) uses only the Python standard library.

## Copyright

ASD-STE100 is copyright ASD (Aerospace, Security and Defence Industries
Association of Europe). This repository has the tool only. It does not have the
standard, or the data from the standard. Do not add the PDF, the dictionary, or
the generated rule files to this repository or to a public location.
