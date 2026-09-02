---
name: simplified-technical-english
description: Write or rewrite text in ASD-STE100 Simplified Technical English (STE), the controlled-English standard, using the full extracted dictionary and all 65 writing rules plus a compliance checker. Use this skill whenever the user says "STE", "Simplified Technical English", "ASD-STE100", "controlled English", "controlled language", or "plain technical English" — and also when they ask for writing that must be unambiguous for non-native English readers, easy to translate, or written like a maintenance manual or procedure, even if they never name the standard. Applies to a named file, to pasted text, or to the piece of writing already in progress in this session; if the target is unclear, it asks instead of guessing.
---

# Simplified Technical English (ASD-STE100)

STE is a controlled form of English written for aircraft maintenance manuals and
now used wherever a text must be read correctly the first time by people who do
not speak English natively, or must survive translation without drifting. It
works by restricting two things: the vocabulary (one approved word per meaning)
and the grammar (short sentences, active voice, simple tenses).

This skill carries the whole of Issue 9 (2025-01-15): every writing rule and all
2,198 dictionary entries, extracted from the source PDF into files you can read
and a checker you can run.

## What to apply it to

Work out the target before you write anything. Getting this wrong means
rewriting something the user did not want touched.

1. **A file or files named in the request** - use them.
2. **Text pasted into the request** - rewrite that text and return it inline.
3. **Nothing named, but this session has one obvious piece of writing in
   progress** - a document you drafted or edited earlier in the conversation, or
   the file under discussion. Use it, and say plainly which one you chose so the
   user can redirect you.
4. **Nothing named and the target is genuinely ambiguous** - stop and ask. List
   the candidates you can see and let the user pick. Do not rewrite a file on a
   guess, and do not silently pick the most recent one when several are equally
   plausible.

When the user invokes this skill without a target and simply wants to work in
STE from here on, treat it as a standing instruction: **apply STE to the prose
you produce for the rest of the session** — documents, reports, procedures,
comments meant for readers — until the user says otherwise. It does not apply to
your conversational replies to the user, to code, or to quoted source material.

## How to do the work

Rewriting into STE is not word substitution. Rule 9.1 exists precisely because
swapping an unapproved word for an approved one often leaves an ungrammatical or
inaccurate sentence; the usual fix is to restructure the sentence around the
approved verb.

1. **Read the source and decide the writing type.** Procedural text (steps the
   reader performs) follows section 5 and the 20-word limit. Descriptive text
   (explanations, reports, findings) follows section 6 and the 25-word limit. A
   document can contain both.
2. **Rewrite section by section**, applying the rules below. Keep meaning exact:
   STE is about clarity, and a rewrite that loses a qualification or a number
   has failed even if it passes every rule.
3. **Look up every word you are unsure of** (see *Vocabulary* below) rather than
   guessing at what is approved. The dictionary frequently contradicts intuition
   — "ensure", "perform", "may", "however", "avoid", "check" as a verb, and
   "required" are all unapproved.
4. **Run the checker on your rewrite**, fix what it reports, and run it again.
5. **Deliver the rewrite and the report** (see *What to deliver*).

## Vocabulary

The controlled dictionary is the part of STE that cannot be reasoned out from
first principles, so consult it rather than relying on your sense of what is
plain.

- **Structured lookup**: `assets/ste_dictionary.json` has an `approved_forms`
  index (every permitted inflection to the parts of speech it is approved as)
  and full `entries` with meanings, alternatives, and the standard's own
  examples.
- **Reading and grepping**: `references/dictionary.md`, one block per entry.
  `grep -i -A6 '^## ensure' references/dictionary.md` answers most questions.
- **How the dictionary works**, including the recurring-error list and the list
  of approved verbs: `references/dictionary-guide.md`.

Three things about approved words that catch people out:

- A word is approved **as one part of speech only** (rule 1.2). "Test" is an
  approved noun and not an approved verb.
- A word is approved **with one meaning only** (rule 1.3). "Follow" is approved
  in the sense of "come after", never in the sense of "obey".
- Only the **listed inflections** are permitted (rule 1.4), and they are printed
  with each approved entry.

### Words that are not in the dictionary

Rules 1.5, 1.6, and 1.8 allow any word that is a **technical noun** or
**technical verb** of your subject field. The dictionary was written for
aerospace, so it contains none of the vocabulary of housing, finance,
statistics, or policy — and mangling "census tract" or "debt-to-income ratio"
into approved words would make the text less accurate, not more readable, and
would break rule 1.11 by giving one thing two names.

`assets/domain_terms.txt` holds the technical nouns already admitted. When you
meet a legitimate technical term that is not there, **admit it** and keep using
the term. When you meet an ordinary word doing an ordinary job ("utilize",
"prior to", "in order to"), replace it.

Where to admit it depends on whose text you are writing:

- **Work in this repo, or any AEI housing, mortgage, finance, or policy text** —
  add the term to `assets/domain_terms.txt`. That file is the house terminology
  and every run picks it up by default.
- **Work in another project with its own subject field** — write a domain terms file
  in that project and pass it with `--domain-terms`, rather than pushing another
  field's vocabulary into the shared file. A trading ledger, a clinical
  protocol, and a housing report do not share a technical vocabulary, and the
  union of all three admits most of ordinary English. Keep the file beside the
  text it serves, name each section with the rule that justifies it, and deliver
  it with the rewrite:

  ```bash
  python scripts/ste_check.py --domain-terms docs/ste_domain_terms.txt DRAFT.md
  ```

  `--no-default-terms` drops the housing list entirely when the project has
  nothing to do with it.

**Capitalization decides how a term matches.** A term written in lower case
matches any capitalization — `mortgage` admits "mortgage" and "Mortgage", which
is what an ordinary technical noun wants. A term written with a capital in it
matches only that spelling. That is what lets you admit an identifier (rule 8.6)
without opening a hole in the vocabulary check:

```
PASS          # admits the verdict token; "pass" the verb stays disallowed
FIL           # a ticker, not the word "fil"
debt-to-income
```

Write a term in capitals whenever the ordinary lower-case word is one the
dictionary disallows. `--audit-domain-terms` lists what still collides after
you have done that, and every remaining collision is reported as an advisory at
each use.

The test is whether the word names a thing in a subject field, not whether it
sounds technical. "Amortization" is a technical noun. "Substantial" is not.

**Watch for collisions.** Some legitimate technical nouns are also words the
dictionary disallows in another sense: a building *permit* against LET (v), a
Treasury *spread* against APPLY (v), a US *state* against CONDITION (n). These
terms belong in the list — but admitting one must not blind the checker to the
disallowed sense, or every domain you add would punch another quiet hole, and
the union of all domains' jargon approaches ordinary English. So the checker
reports each collision as an advisory naming both senses. Read those and confirm
you meant the noun.

`python scripts/ste_check.py --audit-domain-terms` lists every collision in the
current terms file. Run it after adding a batch of terms.

## The rules

The full text of each section is in `references/rules/`. Read the relevant file
when a case is genuinely unclear — the sections are short, and each carries the
standard's own worked examples. The rule statements themselves are below.

### Section 1 - Words (`references/rules/section-1-words.md`)
- **1.1** Use words that are approved in the dictionary, technical nouns, or technical verbs.
- **1.2** Use approved words only as the specified part of speech.
- **1.3** Use approved words only with their approved meanings.
- **1.4** Use only the approved forms of verbs and adjectives.
- **1.5** You can use words that fall into a technical noun category.
- **1.6** Use a word that is not approved only when it is a technical noun or part of one.
- **1.7** Do not use technical nouns as verbs.
- **1.8** Use technical nouns approved in your company, industry, or subject field.
- **1.9** When you must select a technical noun, use one that is short and easy to understand.
- **1.10** Do not use regional, slang, or jargon words as technical nouns.
- **1.11** Do not use different technical nouns for the same item.
- **1.12** You can use verbs that fall into a technical verb category.
- **1.13** Do not use technical verbs as nouns.
- **1.14** Use American English spelling.

### Section 2 - Multi-word nouns (`references/rules/section-2-multi-word-nouns.md`)
- **2.1** Write multi-word nouns of no more than three words.
- **2.2** When a technical noun has more than three words, write it in full, then give a shorter form or use hyphens between the words you use as one unit.

### Section 3 - Verbs (`references/rules/section-3-verbs.md`)
- **3.1** Use only the verb forms given in the dictionary.
- **3.2** Use only these forms and tenses: infinitive, imperative, simple present, simple past, simple future, and the past participle as an adjective. No perfect tenses, no progressive tenses.
- **3.3** Use the past participle form as an adjective — before a noun, or after "to be", "to become", or "to stay". This is not passive voice.
- **3.4** Do not use auxiliary verbs to make complex verb constructions. "Can", "must", and "will" are the approved ones; "may", "should", "would", "could", and "might" are not.
- **3.5** Use the "-ing" form only as a technical noun or as a modifier in one.
- **3.6** Use the active voice. In descriptive writing you can use the passive only when the agent is unknown.
- **3.7** Use an approved verb to describe an action, not a noun or another part of speech. ("Do a test", not "carry out a test procedure".)

### Section 4 - Sentences (`references/rules/section-4-sentences.md`)
- **4.1** Write short and clear sentences.
- **4.2** Do not omit words or use contractions to make sentences shorter.
- **4.3** Use a vertical list for complex texts.
- **4.4** Use connecting words and phrases to connect sentences on related topics.
- **4.5** Use an article ("the", "a", "an") or a demonstrative adjective ("this", "these") before a noun or multi-word noun when applicable.

### Section 5 - Procedural writing (`references/rules/section-5-procedural-writing.md`)
- **5.1** Write short sentences. **Maximum 20 words** in each sentence, warnings and cautions included.
- **5.2** Write only one instruction in each sentence, unless two actions occur at the same time.
- **5.3** Write instructions in the imperative (command) form.
- **5.4** When the reader must know a condition first, start with the descriptive statement, then a comma, then the command.
- **5.5** Write notes to give information, never instructions.

### Section 6 - Descriptive writing (`references/rules/section-6-descriptive-writing.md`)
- **6.1** Give information gradually.
- **6.2** Use key words and key phrases to give the text a logical structure.
- **6.3** Write short sentences. **Maximum 25 words** in each sentence.
- **6.4** Use paragraphs to show related information.
- **6.5** Make sure each paragraph has only one topic.
- **6.6** Make sure no paragraph has more than six sentences.

### Section 7 - Safety instructions (`references/rules/section-7-safety-instructions.md`)
- **7.1** Use a word such as "warning" or "caution" to identify the level of risk. A *warning* is a risk of injury or death; a *caution* is a risk of damage to objects.
- **7.2** Start a safety instruction with a clear and accurate command or condition — not with a description.
- **7.3** Give an explanation that shows the risk or the possible result.
- Put the safety instruction **before** the step it applies to.

### Section 8 - Punctuation and word count (`references/rules/section-8-punctuation-and-word-count.md`)
- **8.1** You can use all standard punctuation except the **semicolon**.
- **8.2** Use hyphens to connect words that are directly related.
- **8.3** Parentheses are permitted for references, item identifiers, work-step identifiers, abbreviations, singular/plural forms, explanations, and alternatives.
- **8.4** In a vertical list, a colon ends a sentence for word-count purposes.
- **8.5** Text in parentheses counts as one word.
- **8.6** Each of these counts as one word: numbers, numbers with units, abbreviations, alphanumeric identifiers, quoted text, titles and headings, and proper nouns of people, groups, organizations, and places.
- **8.7** Hyphenated words count as one word.

### Section 9 - Writing practices (`references/rules/section-9-writing-practices.md`)
- **9.1** Use a different sentence construction when a word-for-word replacement is not sufficient.
- **9.2** Use each approved word correctly, in its approved meaning.
- **9.3** When you use two words together, do not make phrasal verbs. ("Do the test again", not "carry out the test again".)
- **9.4** Use a consistent style for terminology and wording throughout.

### General recommendations (`references/rules/general-recommendations.md`)
Not rules, but they prevent the usual mistakes: keep the conjunction "that"
(GR-1); be careful with "with" (GR-2); make pronouns unambiguous and prefer
repeating the noun (GR-3, GR-4); watch for false friends (GR-5); avoid Latin
abbreviations such as "e.g." and "i.e." (GR-6); use inclusive language (GR-7);
prefer "of" to the possessive form (GR-8).

### One thing STE does not require

The standard prints its own STE examples in capitals. That is a typographic
convention of the document, not a rule. Write normally.

## The checker

```bash
python scripts/ste_check.py DRAFT.md                    # full report
python scripts/ste_check.py --violations-only DRAFT.md  # just the certain findings
python scripts/ste_check.py --mode procedural STEPS.md  # force the 20-word limit
python scripts/ste_check.py --json DRAFT.md             # machine-readable
python scripts/ste_check.py --domain-terms terms.txt DRAFT.md
python scripts/ste_check.py --all-advisories DRAFT.md   # one line per unknown word
```

It reads the extracted dictionary, so its vocabulary verdicts come from the
standard, not from a heuristic. It separates two kinds of finding, and the
distinction matters when you decide what to act on:

- **Violations** — the standard settles these on its own: the dictionary marks
  the word unapproved, the sentence is over the limit, there is a semicolon, a
  contraction, a perfect or progressive tense, a passive with a named agent.
  Fix all of them.
- **Advisories** — these need a judgment the script cannot make without a
  part-of-speech tagger: whether an unknown word is a technical noun, whether a
  gerund is doing the work of a technical noun (permitted by rule 3.5) or of a
  verb, whether "is closed" describes a condition (permitted by rule 3.3) or
  hides an actor. Read them, decide, and act where they are right. A long
  advisory list is normal and is not a failure.

  A rule 3.5 advisory names the verb it found: *"wiring" is the "-ing" form of
  WIRE*. The checker settles whether a word is a gerund at all by removing the
  suffix and looking the stem up in the dictionary's verbs, so "ceiling" and
  "nothing" are not reported as gerunds. Where the stem is a verb the standard
  does not carry, the word is reported under rule 1.6 instead — the same
  question, asked once.

  Most of the list is one advisory — rule 1.6, "this word is not in the
  dictionary" — which fires on every ordinary English word STE has no entry for.
  Those collapse into a wrapped list of words at the end of the report, to scan
  rather than read line by line, because each carries the same suggestion. The
  list comes in two parts: words that repeat, or are hyphenated, or carry a
  capital that is not just the start of a sentence, are the likely domain terms and come first; lower-case words used once are most likely ordinary
  English the standard omits. That split is a guess about shape and frequency,
  not a lexicon, so a technical noun can land in the second list — read it too,
  just faster.

  The advisories printed one per line above that block are the ones that say
  something specific: a domain term that collides with a disallowed sense (rule
  1.2 and the collision half of 1.6), a gerund (3.5), a noun cluster over three
  words (2.1), a passive that may be hiding an actor (3.6). Read those.
  `--all-advisories` expands the word list back to one line and one location per
  word.

Three limits worth knowing so you read the output correctly. The checker cannot
tell a technical noun from an unapproved verb of the same spelling when no
article precedes it, so it may report a genuine domain term as a violation —
that is what the domain terms are for; admit the term and re-run. It reads
markdown structure but not all of markdown's semantics: it splits a table into
cells so a row is not read as one run-on sentence, and it treats a colon as a
sentence end only inside a vertical list (rule 8.4), but a definition list, or a
list written without blank lines, can still land its sentences in the wrong
paragraph for rule 6.6. And it cannot judge whether your rewrite still says what
the original said. That is your job.

Do not treat a violation as settled because the script printed it. The rules
overlap, and where two of them meet the script takes the narrower reading: rule
3.3 makes a past participle after "to be" an adjective, so "the leg is missing"
is correct STE and not a passive; rule 4.3 recommends a vertical list, so the
items of one list are separate paragraphs under rule 6.6. If a finding
contradicts a rule you have read, read the rule file and trust the standard.

After changing either script, run `python scripts/selftest.py`. It does two
things. It rebuilds two corpora from the standard's own STE and non-STE examples
and asserts that the checker still separates them (currently about 1% of words
flagged in correct STE against about 10% in non-STE). And it runs a set of rule
cases, each pairing text where a rule must fire with text where it must stay
quiet — a real paragraph against a vertical list for rule 6.6, "is running"
against "is missing" for 3.2, a noun cluster against a clause for 2.1.

The corpus number is a statistic and moves by a fraction of a percent for most
single-rule changes, so it cannot on its own tell you a rule still works. The
cases can. **When you change a rule, add the pair** — the text that must be
reported and the text that must not. A rule is easy to silence by accident while
chasing a false positive, and the second half of the pair is what catches it.

## What to deliver

Unless the user asks for something else:

1. **The rewritten text** — to a new file beside the original
   (`report.md` → `report.ste.md`) so nothing is lost. Rewrite in place only
   when the user asks for that. For pasted text, return it inline.
2. **A short report of what changed and why**, grouped by rule, citing rule
   numbers. Not a line-by-line diff — the patterns are what teach the user
   something:

   ```
   Rule 1.1 (unapproved words): ensure -> make sure (7x), utilize -> use (4x),
     however -> but (3x), prior to -> before (2x)
   Rule 6.3 (sentence length): split 11 sentences over 25 words; longest was 49
   Rule 3.6 (active voice): rewrote 8 passive constructions to name the actor
   Rule 8.1: replaced 3 semicolons with periods
   Technical nouns kept under rule 1.6: census tract, debt-to-income, LTV
     (added 2 new terms to assets/domain_terms.txt)
   ```

3. **The checker result on the rewrite**, and if anything is left unresolved,
   say what and why. Do not report the work as compliant without having run it.

If the rewrite forced a change you are not sure about — an ambiguity in the
original that STE makes you resolve one way or the other — flag that sentence
explicitly. Controlled language turns vagueness into a decision, and the user
should know which decisions you made on their behalf.

## Rebuilding the reference data

Everything in `references/rules/`, `references/dictionary*.md`, and
`assets/ste_dictionary.json` is generated from the source PDF:

```bash
python scripts/build_dictionary.py
```

It prints a validation summary (entry counts per letter, spot checks against
known pages, unparsed cells) and is the thing to re-run if a new issue of
ASD-STE100 is published. The PDF is copyright ASD; keep this skill and its
extracted files private.
