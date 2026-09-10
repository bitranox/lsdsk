# skill-writer review: a snapshot and the history store carry the hostname

The skill said a snapshot and the history store contain drive serial numbers,
and nothing more. Both also hold the machine's hostname, and the passage that
tells a reader to send a snapshot with a ticket said nothing about what the file
identifies.

## The behavioural arm could not fail, so it is replaced

`redcheck --corpus-cascade` reports inherited coverage, and adjudicated against
its sources the hit is genuine rather than shared vocabulary: this repo's
`CLAUDE.md` tells fixture authors to "rewrite the capture's own `hostname`
field", so an agent working here already knows a capture carries a hostname. An
agent asked what a snapshot identifies could answer correctly from that without
the skill saying it. The other documents it names use the word only in
unrelated senses.

The evidence is therefore a text check of the skill file, plus a quote-back that
accepts only a verbatim quote from the file or the single word NONE. An agent
cannot quote a sentence the file does not contain, whatever it already knows.

## Both claims verified against the source first

- A capture carries `hostname` as a top-level field, in every committed fixture.
- The history store's schema carries `hostname` (`HistoryFile` in
  `adapters/history/store.py`), and the comment beside it says the store holds
  every drive's serial number "exactly as a snapshot does".

## RED

Text check: the only sentence saying what a snapshot or the history store
contains named serial numbers alone. The other `hostname` hits in the file are
the history store refusing a different machine, the default path carrying no
hostname, and a shell `$(hostname)` in an example. The ticket passage made no
statement about identity.

Quote-back, four questions, before the edit:

- What a snapshot contains that identifies a machine or its drives: the
  serial-numbers sentence.
- A sentence saying a snapshot contains the hostname: NONE.
- A sentence saying the history store contains the hostname: NONE.
- A warning in the passage that says to send a snapshot with a ticket: NONE.

## GREEN

Two edits. The content statement names serial numbers and the hostname for both
files, and the ticket passage warns at the point a reader is told to send one,
matching `snapshot --help` and the notice it prints on write.

The same four questions, with the same model and wording, against the edited
file:

- What a snapshot contains: the content statement, now naming the hostname.
- Snapshot and hostname: quotes the content statement.
- History store and hostname: quotes the content statement.
- The ticket warning: quotes the new warning.

Diffed against RED in both directions: every NONE is now a verbatim quote, and
the one answer that was already a quote still is, so nothing the baseline
produced is lost.

## Declared

- No `Skill gaps` list. The quote-back format admits only a quote or NONE, and
  the behavioural arm that would have produced one is the arm inherited context
  invalidated.
- The ticket-passage warning goes one sentence beyond the backlog item's
  recorded scope. It rests on the same point-of-use reasoning that put the
  notice in `snapshot --help` and on stderr.

## Checklist

- [x] RED recorded on both arms before any text changed
- [x] Inherited-coverage check run and adjudicated against its sources, with the replacing route stated
- [x] Both claims in the new sentence verified against the source, not inferred
- [x] GREEN asked the same four questions, with the same model and wording, as RED
- [x] Every NONE became a verbatim quote of the new text
- [x] GREEN diffed against RED in both directions; nothing lost
- [x] Frontmatter untouched, so no routing keyword moved
- [x] No hostnames, addresses or machine paths added; the `$(hostname)` in the skill is a shell expansion, not a value
- [x] Plugin version bumped, since the push is what reaches installs
