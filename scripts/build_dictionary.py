#!/usr/bin/env python
"""Rebuild every derived reference file from the ASD-STE100 source PDF.

Everything the skill relies on -- the structured dictionary, the grep-able
dictionary dump, the per-section writing rules -- is generated here so the
extraction stays auditable. If a future issue of the standard arrives, drop the
new PDF in references/ and re-run this.

The dictionary pages are a four-column table. The table has no vertical rules,
but each row separator is drawn as one horizontal segment per cell, so the
segment endpoints give the column boundaries and the segment y-values give the
row bands. That geometry is what makes cell reconstruction reliable rather than
a whitespace guess.

Usage:
    python scripts/build_dictionary.py [--pdf PATH] [--out-dir PATH]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz  # PyMuPDF

# --- Source layout (1-based PDF page numbers, ASD-STE100 Issue 9) -------------

RULES_FIRST_PAGE = 43
RULES_LAST_PAGE = 128
DICT_GUIDE_FIRST_PAGE = 131
DICT_GUIDE_LAST_PAGE = 148
DICT_FIRST_PAGE = 149
DICT_LAST_PAGE = 434

RULE_SECTIONS = [
    ("section-1-words", "Section 1 - Words", 45, 62),
    ("section-2-multi-word-nouns", "Section 2 - Multi-word nouns", 63, 66),
    ("section-3-verbs", "Section 3 - Verbs", 67, 76),
    ("section-4-sentences", "Section 4 - Sentences", 77, 86),
    ("section-5-procedural-writing", "Section 5 - Procedural writing", 87, 94),
    ("section-6-descriptive-writing", "Section 6 - Descriptive writing", 95, 102),
    ("section-7-safety-instructions", "Section 7 - Safety instructions", 103, 106),
    ("section-8-punctuation-and-word-count", "Section 8 - Punctuation and word count", 107, 114),
    ("section-9-writing-practices", "Section 9 - Writing practices", 115, 122),
    ("general-recommendations", "General recommendations", 123, 128),
]

# Running headers/footers repeated on every page; they are noise in the output.
BOILERPLATE = re.compile(
    r"^(ASD-STE100 Simplified Technical English|Issue \d+|\d{4}-\d{2}-\d{2}"
    r"|Part [12] [-–] .*|Page \d+-\d+-[A-Za-z0-9]+)\s*$"
)

UNICODE_FIXES = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "•": "-", "…": "...",
    " ": " ",
}

# Part-of-speech tags used in the dictionary.
POS_TAGS = "n|v|adj|adv|prep|conj|pron|art|TN|TV|pref|prefix|suffix|abbr|int"
HEADWORD_RE = re.compile(rf"^(?P<word>.+?)\s*\((?P<pos>{POS_TAGS})\)\s*(?P<rest>.*)$")


def clean(text: str) -> str:
    for bad, good in UNICODE_FIXES.items():
        text = text.replace(bad, good)
    # Symbol/Wingdings glyphs land in the private use area; they carry no text.
    text = re.sub(r"[-]", "", text)
    return text


def strip_boilerplate(page_text: str) -> str:
    lines = [ln.rstrip() for ln in clean(page_text).splitlines()]
    return "\n".join(ln for ln in lines if not BOILERPLATE.match(ln.strip()))


# --- Dictionary table extraction ---------------------------------------------


def _merge(values: list[float], tol: float = 3.0) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _cluster(counts: Counter, tol: float = 5.0) -> list[tuple[float, int]]:
    """Group nearby x positions, keeping the total hit count for each group."""
    groups: list[tuple[float, int]] = []
    for x in sorted(counts):
        if groups and x - groups[-1][0] <= tol:
            pos, n = groups[-1]
            groups[-1] = (pos, n + counts[x])
        else:
            groups.append((x, counts[x]))
    return groups


def page_rules(page) -> tuple[Counter, list[float]]:
    """Raw ruling-line evidence: x endpoints of row separators, and their y values."""
    ys: list[float] = []
    xs: Counter = Counter()
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        if rect.height < 3 and rect.width > 3:
            ys.append(round(rect.y0, 1))
            xs[round(rect.x0, 1)] += 1
            xs[round(rect.x1, 1)] += 1
    return xs, _merge(ys, tol=2.0)


def consensus_columns(doc) -> dict[int, list[float]]:
    """Derive one column grid per page parity from the whole dictionary.

    Each row separator is drawn as one segment per cell, so endpoints pile up on
    the real column boundaries. Deriving this per page is fragile -- a nested
    sub-table ("For other meanings, use:") adds competing boundaries on some
    pages -- but the true grid is identical on every page of a given parity, so
    a vote across ~280 pages settles it.
    """
    votes: dict[int, Counter] = {0: Counter(), 1: Counter()}
    for pno in range(DICT_FIRST_PAGE, DICT_LAST_PAGE + 1):
        xs, _ = page_rules(doc[pno - 1])
        for pos, n in _cluster(xs):
            if n >= 5:
                votes[pno % 2][round(pos)] += 1

    grids: dict[int, list[float]] = {}
    for parity, counter in votes.items():
        chosen: list[float] = []
        for pos, _ in counter.most_common():
            if all(abs(pos - c) > 20 for c in chosen):
                chosen.append(float(pos))
            if len(chosen) == 5:
                break
        grids[parity] = sorted(chosen)
    return grids


_HEAD_CACHE: dict[int, list[tuple[float, float]]] = {}


def _in_running_head(page, word) -> bool:
    """True if the word sits on a repeated header/footer line rather than in the table."""
    bands = _HEAD_CACHE.get(page.number)
    if bands is None:
        bands = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = clean("".join(s["text"] for s in line["spans"])).strip()
                if BOILERPLATE.match(text) or text in {
                    "Word", "Approved meaning/", "(part of speech)", "ALTERNATIVES",
                    "STE EXAMPLE", "Non-STE example",
                }:
                    bands.append((line["bbox"][1], line["bbox"][3]))
        _HEAD_CACHE[page.number] = bands
    cy = (word[1] + word[3]) / 2
    return any(y0 - 1 <= cy <= y1 + 1 for y0, y1 in bands)


def extract_bands(page, cols: list[float]) -> list[list[str]]:
    """Reconstruct the four cells of every row band on a dictionary page."""
    _, rows = page_rules(page)
    if len(cols) < 5 or len(rows) < 2:
        return []

    # cols = [left edge, b1, b2, b3, right edge]; interior values split columns.
    bounds = cols[1:-1][:3]
    words = [w for w in page.get_text("words")
             if w[4].strip() and not _in_running_head(page, w)]

    # Anything below the last ruling line still belongs to the final band.
    bottom = max([w[3] for w in words], default=rows[-1]) + 1
    edges = rows + ([bottom] if bottom > rows[-1] + 1 else [])

    bands: list[list[str]] = []
    for i in range(len(edges) - 1):
        y0, y1 = edges[i], edges[i + 1]
        buckets: list[list] = [[], [], [], []]
        for w in words:
            cy = (w[1] + w[3]) / 2
            if not (y0 <= cy < y1):
                continue
            idx = sum(1 for b in bounds if w[0] > b - 2)
            buckets[min(idx, 3)].append(w)
        cells = []
        for bucket in buckets:
            # Sort into visual lines, then left-to-right within each line.
            bucket.sort(key=lambda w: (round(w[1] / 5), w[0]))
            cells.append(clean(" ".join(w[4] for w in bucket)).strip())
        if any(cells):
            bands.append(cells)
    return bands


def split_headword(cell: str) -> tuple[str, str | None, list[str], str] | None:
    """Parse a headword cell into (word, part of speech, inflections, note).

    'ACCEPT (v), ACCEPTS, ACCEPTED' -> ('ACCEPT', 'v', ['ACCEPTS', 'ACCEPTED'], '')
    'BE (v), IS, WAS, (also ARE, WERE) No other verb'
        -> ('BE', 'v', ['IS', 'WAS', 'ARE', 'WERE'], 'No other verb')

    A few approved phrases ('FOR EXAMPLE') and unapproved ones ('such as') carry
    no part-of-speech tag at all, so pos may be None.
    """
    cell = cell.strip()
    m = HEADWORD_RE.match(cell)
    if not m:
        if re.fullmatch(r"[A-Za-z][A-Za-z'-]*(?: [A-Za-z'-]+){0,3}", cell):
            return cell.strip(), None, [], ""
        return None

    word = m.group("word").strip().strip(",")
    pos = m.group("pos")
    rest = m.group("rest").strip()
    # Inflections are printed in capitals; anything else is an editorial note
    # such as "No other verb forms."
    forms = re.findall(r"\b[A-Z][A-Z'-]{1,}\b", rest)
    note = re.sub(r"\b[A-Z][A-Z'-]{1,}\b", " ", rest)
    note = re.sub(r"[(),]", " ", note)
    note = re.sub(r"\s+", " ", note).strip()
    if note.lower().startswith("also"):
        note = note[4:].strip()
    return word, pos, forms, note


def parse_dictionary(doc, report: dict) -> list[dict]:
    entries: list[dict] = []
    skipped_pages: list[int] = []
    grids = consensus_columns(doc)
    report["column_grids"] = grids

    for pno in range(DICT_FIRST_PAGE, DICT_LAST_PAGE + 1):
        page = doc[pno - 1]
        bands = extract_bands(page, grids[pno % 2])
        if not bands:
            # The standard pads letter sections with explicitly marked blanks.
            if "Blank Page" not in page.get_text():
                skipped_pages.append(pno)
            continue

        for cells in bands:
            head, col2, ste_ex, non_ste_ex = cells
            if head.startswith("Word") or col2.startswith("Approved meaning"):
                continue  # repeated column header

            if head:
                # A headword cell can wrap onto following bands. Those bands are
                # blank in every other column, which is how they are recognized
                # -- a real new headword always brings a meaning or an
                # alternative with it.
                is_continuation = not (col2 or ste_ex or non_ste_ex)
                if is_continuation and entries:
                    entries[-1]["note"] = f'{entries[-1]["note"]} {head}'.strip()
                    continue

                parsed = split_headword(head)
                if parsed is None:
                    # Not a headword and not blank elsewhere: still a wrapped
                    # note, just one that shares its band with wrapped col-2 text.
                    if entries:
                        entries[-1]["note"] = f'{entries[-1]["note"]} {head}'.strip()
                    else:
                        report["unparsed_headwords"].append((pno, head))
                else:
                    word, pos, forms, note = parsed
                    # Approved words are printed in capitals with their inflections.
                    approved = word.upper() == word and word.lower() != word
                    entries.append({
                        "headword": word if approved else word.lower(),
                        "pos": pos,
                        "approved": approved,
                        "forms": forms,
                        "note": note,
                        "definition": None,
                        "alternatives": [],
                        "examples": [],
                        "page": pno,
                    })

            if not entries:
                continue
            entry = entries[-1]

            if entry["approved"]:
                if col2:
                    entry["definition"] = (
                        f'{entry["definition"]} {col2}'.strip() if entry["definition"] else col2
                    )
                if ste_ex:
                    entry["examples"].append(ste_ex)
                if non_ste_ex:
                    entry["examples"].append(f"(not STE) {non_ste_ex}")
            else:
                alt = split_headword(col2) if col2 else None
                if alt and alt[1]:
                    # Wrapped guidance can run into the alternative, as in
                    # "more accurate verb. MEASURE (v)". The alternative itself
                    # is always the capitalized run at the end.
                    caps = re.search(r"[A-Z][A-Z' -]*$", alt[0])
                    word = caps.group(0).strip() if caps else alt[0]
                    if word != alt[0]:
                        entry["note"] = (
                            f'{entry["note"]} {alt[0][: -len(word)].strip()}'.strip()
                        )
                    entry["alternatives"].append({
                        "word": word,
                        "pos": alt[1],
                        "ste_example": ste_ex,
                        "non_ste_example": non_ste_ex,
                    })
                elif col2 and col2.upper() == col2 and col2.lower() != col2:
                    # An approved phrase used as an alternative, e.g. FOR EXAMPLE.
                    entry["alternatives"].append({
                        "word": col2,
                        "pos": None,
                        "ste_example": ste_ex,
                        "non_ste_example": non_ste_ex,
                    })
                elif col2:
                    # Free-text guidance, e.g. "Use a different sentence
                    # construction". It wraps across bands, so accumulate it.
                    entry["note"] = f'{entry["note"]} {col2}'.strip()
                elif ste_ex or non_ste_ex:
                    if entry["alternatives"]:
                        last = entry["alternatives"][-1]
                        last["ste_example"] = f'{last["ste_example"]} {ste_ex}'.strip()
                        last["non_ste_example"] = f'{last["non_ste_example"]} {non_ste_ex}'.strip()

    report["skipped_pages"] = skipped_pages
    return entries


def merge_duplicate_entries(entries: list[dict]) -> list[dict]:
    """A single headword can be split across a page break; stitch those halves."""
    merged: list[dict] = []
    index: dict[tuple[str, str, bool], dict] = {}
    for e in entries:
        key = (e["headword"].lower(), e["pos"], e["approved"])
        if key in index:
            target = index[key]
            target["alternatives"].extend(e["alternatives"])
            target["examples"].extend(e["examples"])
            if e["definition"]:
                target["definition"] = (
                    f'{target["definition"]} {e["definition"]}'.strip()
                    if target["definition"] else e["definition"]
                )
            for f in e["forms"]:
                if f not in target["forms"]:
                    target["forms"].append(f)
            if e["note"]:
                target["note"] = f'{target["note"]} {e["note"]}'.strip()
        else:
            index[key] = e
            merged.append(e)
    return merged


def build_approved_forms(entries: list[dict]) -> dict[str, list[str]]:
    """Every usable surface form -> the parts of speech it is approved as.

    The dictionary prints each approved word with its inflections, so the
    checker can match surface forms directly and needs no lemmatizer.
    """
    forms: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        if not e["approved"]:
            continue
        for surface in [e["headword"]] + e["forms"]:
            for token in re.findall(r"[A-Za-z][A-Za-z'-]*", surface):
                forms[token.lower()].add(e["pos"] or "phrase")
    return {k: sorted(v) for k, v in sorted(forms.items())}


# --- Output writers -----------------------------------------------------------


def write_dictionary_md(entries: list[dict], path: Path) -> None:
    lines = [
        "# ASD-STE100 Issue 9 - Dictionary (extracted)",
        "",
        "Generated by `scripts/build_dictionary.py`. One block per entry.",
        "APPROVED entries list their permitted inflections. Unapproved entries",
        "list the approved words to use instead.",
        "",
    ]
    for e in entries:
        status = "APPROVED" if e["approved"] else "NOT APPROVED"
        head = e["headword"] + (f' ({e["pos"]})')
        lines.append(f"## {head} - {status}")
        if e["forms"]:
            lines.append(f'Forms: {", ".join(e["forms"])}')
        if e["definition"]:
            lines.append(f'Meaning: {e["definition"]}')
        for alt in e["alternatives"]:
            pos = f' ({alt["pos"]})' if alt["pos"] else ""
            lines.append(f'Use instead: {alt["word"]}{pos}')
            if alt["ste_example"]:
                lines.append(f'  STE: {alt["ste_example"]}')
            if alt["non_ste_example"]:
                lines.append(f'  not STE: {alt["non_ste_example"]}')
        for ex in e["examples"]:
            lines.append(f"  {ex}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_rules(doc, out_dir: Path, report: dict) -> None:
    rules_dir = out_dir / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    for slug, title, first, last in RULE_SECTIONS:
        body = "\n".join(strip_boilerplate(doc[p - 1].get_text())
                         for p in range(first, last + 1))
        body = re.sub(r"\n{3,}", "\n\n", body).strip()
        text = f"# {title}\n\nASD-STE100 Issue 9, Part 1 - Writing rules " \
               f"(source pages {first}-{last}).\n\n{body}\n"
        (rules_dir / f"{slug}.md").write_text(text, encoding="utf-8")
        report["rule_files"].append((slug, len(body.split())))


def write_dictionary_guide(doc, out_dir: Path) -> None:
    body = "\n".join(strip_boilerplate(doc[p - 1].get_text())
                     for p in range(DICT_GUIDE_FIRST_PAGE, DICT_GUIDE_LAST_PAGE + 1))
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    text = ("# Guide to the dictionary\n\nASD-STE100 Issue 9, Part 2 introduction "
            f"(source pages {DICT_GUIDE_FIRST_PAGE}-{DICT_GUIDE_LAST_PAGE}). "
            "Includes how to select words, the list of recurring errors, and the "
            f"list of approved verbs.\n\n{body}\n")
    (out_dir / "dictionary-guide.md").write_text(text, encoding="utf-8")


# --- Validation ---------------------------------------------------------------


def validate(entries: list[dict], forms: dict, report: dict) -> int:
    print("\n=== Extraction summary ===")
    approved = [e for e in entries if e["approved"]]
    unapproved = [e for e in entries if not e["approved"]]
    print(f"entries          : {len(entries)}")
    print(f"  approved       : {len(approved)}")
    print(f"  not approved   : {len(unapproved)}")
    print(f"approved forms   : {len(forms)}")

    by_letter = Counter(e["headword"][0].upper() for e in entries if e["headword"])
    print("per letter       : " + "  ".join(
        f"{k}={by_letter[k]}" for k in sorted(by_letter)))

    problems = 0

    if report["skipped_pages"]:
        problems += 1
        print(f"FAIL  {len(report['skipped_pages'])} dictionary pages yielded no table: "
              f"{report['skipped_pages'][:12]}")
    else:
        print("ok    every dictionary page yielded a table")

    if report["unparsed_headwords"]:
        problems += 1
        print(f"FAIL  {len(report['unparsed_headwords'])} unparsable headword cells, e.g. "
              f"{report['unparsed_headwords'][:5]}")
    else:
        print("ok    every headword cell parsed")

    # The dictionary is alphabetical, so a big regression means a mis-assigned
    # cell. It is not consistent about whether a space sorts before letters
    # ("aft of" after "after", but "consist of" before "consistent"), so a pair
    # counts as a regression only if it is out of order under both collations.
    def collate(word: str) -> tuple[str, str]:
        letters = re.sub(r"[^a-z0-9]", "", word.lower())
        spaced = re.sub(r"[^a-z0-9 ]", "", word.lower())
        return letters, spaced

    regressions = []
    prev = ("", "")
    for e in entries:
        cur = collate(e["headword"])
        if cur[0] < prev[0] and cur[1] < prev[1]:
            regressions.append((prev[0], cur[0], e["page"]))
        prev = cur
    if regressions:
        print(f"WARN  {len(regressions)} alphabetical regressions, e.g. {regressions[:5]}")
    else:
        print("ok    headwords are in alphabetical order")

    empty = [e["headword"] for e in entries
             if not e["approved"] and not e["alternatives"]]
    if empty:
        print(f"WARN  {len(empty)} unapproved entries with no alternative: {empty[:8]}")
    else:
        print("ok    every unapproved entry offers an alternative")

    no_def = [e["headword"] for e in approved if not e["definition"]]
    if no_def:
        print(f"WARN  {len(no_def)} approved entries with no meaning: {no_def[:8]}")
    else:
        print("ok    every approved entry has a meaning")

    # Spot checks against pages read by hand.
    lookup = {(e["headword"].lower(), e["pos"]): e for e in entries}
    checks = [
        ("abandon", "v", False, ["GO", "STOP"]),
        ("abut", "v", False, ["TOUCH"]),
        ("accelerate", "v", False, ["INCREASE", "FASTER"]),
        ("accept", "v", True, None),
        ("alignment", "n", False, ["ALIGN"]),
        ("allowable", "adj", False, ["PERMITTED", "APPROVED"]),
    ]
    for word, pos, want_approved, want_alts in checks:
        e = lookup.get((word, pos))
        if e is None:
            problems += 1
            print(f"FAIL  spot check: {word} ({pos}) missing")
            continue
        if e["approved"] != want_approved:
            problems += 1
            print(f"FAIL  spot check: {word} approved={e['approved']}, want {want_approved}")
            continue
        if want_alts is not None:
            got = [a["word"] for a in e["alternatives"]]
            if got != want_alts:
                problems += 1
                print(f"FAIL  spot check: {word} alternatives {got}, want {want_alts}")
                continue
        print(f"ok    spot check: {word} ({pos})")

    for slug, words in report["rule_files"]:
        if words < 200:
            problems += 1
            print(f"FAIL  rules/{slug}.md only {words} words")
    if all(w >= 200 for _, w in report["rule_files"]):
        print(f"ok    {len(report['rule_files'])} rule files extracted "
              f"({sum(w for _, w in report['rule_files'])} words total)")

    return problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=Path,
                    default=root / "references" / "ASD-STE100_ISSUE9.pdf")
    ap.add_argument("--out-dir", type=Path, default=root)
    args = ap.parse_args()

    doc = fitz.open(args.pdf)
    report = {"skipped_pages": [], "unparsed_headwords": [], "rule_files": []}

    entries = merge_duplicate_entries(parse_dictionary(doc, report))
    forms = build_approved_forms(entries)

    refs = args.out_dir / "references"
    assets = args.out_dir / "assets"
    refs.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)

    (assets / "ste_dictionary.json").write_text(
        json.dumps({
            "source": "ASD-STE100 Issue 9, 2025-01-15",
            "entry_count": len(entries),
            "approved_forms": forms,
            "entries": entries,
        }, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    write_dictionary_md(entries, refs / "dictionary.md")
    write_dictionary_guide(doc, refs)
    write_rules(doc, refs, report)

    problems = validate(entries, forms, report)
    print(f"\nwrote assets/ste_dictionary.json, references/dictionary.md, "
          f"references/dictionary-guide.md, references/rules/*.md")
    if problems:
        print(f"\n{problems} blocking problem(s) -- fix the extractor before shipping.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
