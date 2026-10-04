# Tools

Four small programs for the checking seats. Python 3.12, standard library only; `falsify.py` also needs git.
Each checking seat runs its own copy, kept in its own folder. Paths below are relative to that folder.

## Cards: `tools/card.py`

```
python3 tools/card.py validate <card.json>     # exit 0 when every required field is present
python3 tools/card.py render <card.json>       # the fixed-format text to post in the room
```

A handoff card is a JSON file with these fields:

```json
{"card": "handoff", "stage": 1, "from": "implementer", "to": "falsifier",
 "task": "<the complete task, pasted>",
 "revision_in": "<the revision the work started from>", "revision_out": "<the full committed revision>",
 "commands": [{"cmd": "<command that was run>", "exit_code": 0}],
 "claim": "<one line>", "changed_tests": ["<path>", "<path>"],
 "at": "<time, ISO 8601>"}
```

A pointer to a message in place of the task is refused. A verdict card and a finding card are written by the
two tools below. Post the rendered text, unchanged: the counts in the cost table are read from it.

## The falsifier's revision check: `tools/falsify.py`

```
python3 tools/falsify.py --repo <result repository> --revision <full revision> \
  --ledger accepted.jsonl \
  --test-cmd "<the command the handoff card lists>" --stage <n> --subdir stage-<n> \
  --changed-tests <paths from the card, comma-separated> \
  [--base <the revision the work started from, or empty>] \
  [--spec-change "<path>=<requirement that changed>"] \
  --out cards/s<n>-<short revision>.json
```

| Exit | Meaning |
|---|---|
| 0 | ACCEPT |
| 1 | REJECT |
| 2 | refused before anything ran: wrong arguments, a base or frozen tests that are not the ones the ledger holds, a stage that is not the one of its folder, a command that changes into the stage folder, a place for the card that cannot hold it, a ledger that cannot be read |
| 3 | the check could not run, and no verdict exists |

It prints the card to post. It works on temporary copies: no working folder is touched.

**The command.** Every command runs inside `--subdir`, with no standard input. Give `--test-cmd` as the
command is run from inside the stage folder: `npm test`, not `cd stage-1 && npm test`. A command that
changes into the stage folder is refused with exit 2, because it would fail on its first word whatever the
work is, and that would read as a rejection. A command that waits for input sees its end at once.

**The ledger.** `--ledger` names a file the tool keeps, one line of JSON per verdict. From it the tool takes
the base, which is the last accepted revision, and the frozen tests, which are those of the last revision
accepted for an earlier stage, read in the folder of that stage. With an accepted revision in the ledger,
leave `--base` out. While nothing is accepted, `--base` is required: it is `revision_in` from the card, or
`empty` when nothing came before the work. A rejected revision is never a base, and neither is a revision
built on one: what was rejected would be forgiven. A base that is not the one the ledger holds is refused.
Without `--ledger` the tool takes `--base`, `--frozen-ref` and `--frozen-dir` on trust.

Everything between the base and the revision is judged, however many commits that is.

**The runs.** Up to five, each named on the card with its exit code:

| Run | Has to | Shows |
|---|---|---|
| revision | pass | the claim reproduces |
| base as it was | | how many tests ran before the work, for the comparison below |
| base with delivered tests | fail | the delivered tests need the work |
| revision with implementation reverted | fail | the same, with the rest of the revision in place |
| earlier stage with delivered tests | | made when the base holds no stage folder: on the earlier stage's accepted code the delivered tests find the code they ask about, so a test of a changed behaviour can fail there. What passes there proves nothing and accuses nothing |
| revision with every function emptied | fail | the delivered tests notice when the work does nothing |

The runs after the first two are made when the work changed both test files and other files. In a stage
folder that is new at the base, the runs on the base and with the implementation reverted cannot load the
tests, because the code is not there. They show nothing, and a note on the card says so. The run on emptied
code is the one that counts then: every Python function the work changed or added keeps its name and its
arguments and returns nothing. A revision whose tests still pass is rejected with "passes with the code emptied".

One case is a note and no finding. A delivered test vouches for itself and for no other: a test that is new,
or whose body is not the one its name had, may pass on emptied code when it failed while it ran, by its own
name and in its own file, on code without the work - on the base with the delivered tests, with the
implementation reverted, or on the earlier stage's code. The card names the test and the run that showed it.
Such a test may be right: what it asks for may be that nothing happens, as in a fix that stops a function
from raising. A test that failed in no such run keeps the finding, and the card names it too, with what to
do: deliver such a test with a change whose absence it can show, or strengthen it. A failure that the output
itself puts down to code that was not there (a module, a name, an attribute, an argument, a file) vouches
for nothing. A test that failed beside the hollow one buys it nothing, and neither does a test of the same
name in another file: where the runner names no file, a name declared fresh in two files is vouched in
neither. For pytest a vouching is granted only by the runner's own summary section, and the log is read only
up to the runner's last closing line: what a test prints at interpreter shutdown stands after it and is not
read. A failure block that a test prints into its output can take a vouching away, never grant one. A made-up
unittest failure block is believed, and so is a closing line forged after the real one, like a made-up
summary. The vouching matches the file the runner names, in the two exact forms it writes, from the stage
folder and from the repository root: a command that runs the tests from anywhere else denies the vouching
and errs strict.

The run on emptied code is judged as a whole. The card says how many tests failed in it, which functions were
emptied, and which delivered tests are not named among the failures. The last note of the card says what
showed that the delivered tests can fail, or that nothing did.

**The test files.** What counts as a test file: everything under a tests folder, fixtures included, and files
named as tests. Each changed one must be listed in the card, and so must the configuration that decides which
tests run. A file that was only copied forward from the folder of the earlier stage need not be listed.

Two comparisons do not depend on how a test was switched off. In each changed test file, a
test that was there and is gone, removed or renamed, is a finding, unless the file carries a note. Read for
this are the files a runner collects by itself (`test*.py`, `*_test.py`, every JavaScript or TypeScript file
under a tests folder or named as a test, `*_test.go`), and any other Python file under a tests folder when it
holds a TestCase or the project widens what is collected. Shared fixture files, fixtures and helpers that the
file itself calls are not tests.

And the tool reads the summary the test runner prints on the revision and on the base as it was: fewer tests
run, more left out, or no summary where the base printed one, is a finding. One runner is read per run, the
one whose summary comes last. pytest, unittest, node and `go test -v` were tried against the real runners; the
forms for jest, mocha and cargo are written from their documentation. The two runs are compared only when
they end with the same runner. Every summary of that runner is added up, since a command may run it twice.

**Frozen tests.** A stage folder is the earlier one copied and widened, so the frozen tests are compared by
what they declare, not byte for byte. A frozen test that is gone, whose body changed, that is defined once
more below, with fewer assertions or more skip markers, is a finding. A test that was added is fine. A helper,
a fixture or a table of cases that changed beside the tests is said in a note. Adding an assertion to a
frozen test changes its body and needs a note like any other change.

**Outside the stage folder.** The work is judged in `--subdir`. Every file it changed outside is named in a
note. A test file or test configuration outside that was changed or deleted is a finding, unless it carries a
`--spec-change` note, is the same as the file of its name inside `--subdir`, or the ledger holds the revision
as accepted for the stage of that folder. A revision that mends two stage folders is checked once per folder,
the earlier stage first.

**The card at `--out`.** A card that stands there when a check begins is moved to `<out>.earlier`, which
holds only the last card displaced this way; the ledger keeps every verdict. After a check that was refused
or could not run, no card stands at `--out`. Without `--out`, the run logs land in a `falsify-logs-*` folder
under the temporary directory and are not cleaned up.

**What it cannot see.**

- It trusts the command the card lists, its exit code and its output. A made-up summary printed after the
  real one is believed.
- Only Python function bodies are emptied. Code at the top of a module, in a lambda, in a class body, in
  data files or templates, and all code in other languages stays as it is. For work in another language
  the run is not made, and a note says so.
- A test that fails on emptied code need not test behaviour: `assert result is not None` fails there too.
- The names of failed tests are read from pytest, unittest, node and `go test -v` output. With another
  runner no test vouches for itself, and a passing emptied run stays a finding.
- Two tests of one name in one file vouch together: the runner names the file, not the class.
- A delivered test that can fail on no tree, such as an invariant that holds everywhere, keeps the
  finding when it arrives beside a does-nothing fix. Deliver it separately.
- A test or test configuration added outside the stage folder is named in a note, never a finding.
- An honest revision that mends two stage folders differently is rejected for both; deliver it one stage
  at a time, or give the changed test a `--spec-change` note. Mended the same way in both, it passes.
- One test that notices makes the run on emptied code fail, whatever the other tests do. The note names the
  delivered tests that are not among the failures; it is no finding.
- A test can tell the trees apart, by the name of its folder or by reading the source.
- A frozen test wrapped in a condition that is never true is not seen. In JavaScript a changed body under
  the same name is a note only.
- Within one stage the body of a test may change freely if the file is listed and the number of assertions
  does not fall.
- It counts assertions by pattern. A test hidden by a line in a general project file (`setup.cfg`,
  `pyproject.toml`, `package.json`) or by being turned into a nested function is seen only through the
  count of tests that ran, which added tests can make up for. In a stage folder that is new at the base
  there is no earlier count to compare with.
- A test renamed away is not missed when another changed file, or another class in the same file, declares
  a test of the same name. With `go test -v` every line that starts like a result counts, also one the
  program printed.
- The ledger is a plain file. A line typed by hand is believed, and a ledger that was deleted takes the base
  with it.

Whenever the numbers of tests were not compared, the verdict carries a note that says so and why. A note
given with `--spec-change` excuses a changed frozen test or a lower assertion count for that file, and is
printed in the verdict for the coordinator to read.

`scripts/verify-falsify` in the kit's repository shows five cases on a practice repository: honest work
accepted in a first folder and in one copied forward, emptied code and a deleted test rejected, a wrong base
refused.

## The verifier's check list: `tools/speccheck.py` and `tools/CHECKS.md`

```
python3 tools/speccheck.py coverage --checks checks/stage-<n>.jsonl --spec <requirements file> [<file> ...]
python3 tools/speccheck.py run --checks checks/stage-<n>.jsonl --base-url http://127.0.0.1:<port> \
  --stage <n> --cards cards/stage-<n> --spec <requirements file> [<file> ...] \
  --accepted <the falsifier's verdict card of the revision, as a .json file>
python3 tools/speccheck.py replay <card.json> --base-url http://127.0.0.1:<port>
python3 tools/speccheck.py withdraw <card.json> --reason "<why the check was wrong>"
```

| Exit | Meaning |
|---|---|
| 0 | every entry that can run passed |
| 1 | at least one entry failed; with `--cards`, its card was written the moment it failed |
| 2 | the check list, the card, a requirements file or the address is unusable: nothing was judged. An entry that expects nothing, an unknown field, a sentence that is in no requirements file, or a list with nothing to run all end here |
| 3 | the service could not be reached. That is an error, not a finding; cards already written are kept and named |

**Measured against the requirements.** With `--spec` every quoted sentence is looked up in the requirements
files. A sentence that stands in none of them makes the whole list unusable: exit 2, and nothing is sent.
Matching is exact in capitals, punctuation, dashes and quotation marks; list markers, heading marks, emphasis
and back-ticks are left out, and a final full stop may be left out. A row of a table is quoted cell by cell,
or as the row with its bars. A part of a sentence is found, gets a note, and counts for no line. The coverage
line then carries two figures, and both have to be read: how many sentences of the list have an executable
check, and how many lines of the requirements that hold a requirement word (must, shall, never, exactly, at
most, at least, only) are quoted by no entry. An entry listed as `unchecked` counts as quoting its lines.
Without `--spec` the list is measured against itself.

**The accepted revision.** With `--accepted` the run takes the falsifier's verdict card and refuses one that
is not an ACCEPT. A card written by hand with the right fields is taken: the tool cannot tell. `replay`
has no such flag.

**Withdrawing a card.** A finding whose check misread the requirements is withdrawn, never deleted. Only an
open card can be withdrawn. The reason has 20 characters or more, is stored in the card on one line, and is
masked like everything else in a card. Nothing takes a withdrawal back.

A request has 5 seconds in all: sending, headers and the last byte of the answer. A burst is released together and each
request then connects and sends, as a caller would. `CHECKS.md` describes an entry.

What a card leaves out, so that it can be committed: a value saved from a response and then sent in a header,
or saved under a name such as token or password, is written as `{{name}}`; a value the service returns under
such a name is masked, wherever the word stands in the name, unless the name ends in a word that only
describes (`tokens_used`, `password_policy`); the shapes a public repository is scanned for are masked. What a card keeps: every
other value, so that it still says what was observed. A credential the service returns under a name that
does not say so stays in the card: read a card before you commit it. A check list that holds a value shaped
like a credential is refused, because the list is committed too. One shape cannot be helped: NAME=value with a
name such as token or password, in a form body or an address. The organizers' checker reads it as a credential
in a `.json` file even when the value is a placeholder or masked. The tool warns when a list holds it; it
cannot warn when only an answer holds it. In an answer that is not JSON, only name and value pairs are
masked: a credential inside a script or a page stays. An answer above two million characters is masked as
text, not as JSON.

Start the service the way its run instructions say, where only you can reach it, before running the list.

## Before submitting: `tools/preflight.py`

```
python3 tools/preflight.py <result repository> --track <track> [--harness <kickoff checkout>] [--costs-out costs.json]
```

| Exit | Meaning |
|---|---|
| 0 | ready; warnings may be listed |
| 1 | problems are listed |
| 2 | the path is unusable |
| 3 | this tool failed, and nothing was judged |

Lists gate problems, run fields still unfilled, how the work was spread across seats, human messages in the
room, how many of the posted cards trace to a tool run, and the cost table. `--costs-out` saves the table as
JSON. Lines that start with `problem:` are this tool's; lines that start with `official:` come from the
organizers' checker. Both have to be cleared.

A card traces when a tool result of the seat that posted it, earlier in the room, holds the first line of the
card and below it the lines that say what the card decided: the verdict and the revision, the check line of a
finding, or the revision of a handoff. A card that a seat typed itself does not trace, and is listed with the
time and the seat. A line that starts like a card and is not in the form the tools print is no card: it is
listed, and counted nowhere. The cost table has a column for withdrawn findings and one for traced cards.

What it cannot see: a seat can run the tool and then post another text under the same first lines; only the
lines named above are compared. The room is read in the form BAND Desktop downloads it in; six downloads
of other teams were read without an error before the run.
