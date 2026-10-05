# Factory

All four stages, every one accepted: 9 h 42 m of running time from the first dispatch to the
last commit (25 h 04 m wall clock, of which a single 15.4-hour overnight stall), on six human
messages. Model spend: [add from the Claude account].

This factory has four seats. One coordinates, one builds and two check. The checking seats
take nothing on the builder's word. One re-runs every committed revision on its own copy and tries to make
the delivered tests fail. The other holds only the written requirements and the running
service. Bad work comes back as a card holding the exact requests to replay, and the card
closes only when that replay passes.

## Seats

| Seat | Owns | Works in | Harness and model |
|---|---|---|---|
| coordinator | the plan for the stage, every handoff, the final report | the room; it writes no code | first lines of `mandates/coordinator.md` |
| implementer | the product code and its own tests | the result repository, inside the stage folder | first lines of `mandates/implementer.md` |
| falsifier | the verdict on each committed revision | its own folder, on temporary copies of the repository | first lines of `mandates/falsifier.md` |
| verifier | the check list made from the written requirements, and the findings | its own folder, against the running service | first lines of `mandates/verifier.md` |

The seat instruction files name no track. Everything about the problem arrives in the
dispatch, which is the one message a human sends per stage.

## Stand it up

You need Python 3.12 or newer, Git, a running Docker daemon, a Band account and Claude Code
signed in. The seats are Band agents that run as processes on your machine, through `band-sdk`.
The commands are in `run/README.md`.

1. Make a workspace folder. Put the kickoff checkout in it and set up its virtual
   environment as the organizers' guide says. Beside it make `band-work/result`,
   `band-work/falsifier`, `band-work/verifier` and `band-work/checks`. Run
   `git init -b main` in `band-work/result`.
2. Copy `mandates/`, `tools/`, `dispatch/` and `run/` from this repository into
   `band-work/result` and commit them under your own name.
3. Give each checking seat its own clone and its own tools: clone `band-work/result` into
   `band-work/falsifier/result` and `band-work/verifier/result`, and copy `tools/` into
   `band-work/falsifier/` and `band-work/verifier/`.
4. Change the `Harness:` and `Model:` lines in `mandates/` to what each seat really runs.
   Put your Band key into `band.env` in the workspace, outside every repository, and
   register four seats named exactly `coordinator`, `implementer`, `falsifier` and
   `verifier` with `run/room.py register`.
5. Start the seats with `run/seats.sh start real`. It runs `run/run-seat.py` once per seat:
   the implementer and the coordinator in `band-work/result`, the falsifier in
   `band-work/falsifier`, the verifier in `band-work/verifier`, each with its file from
   `mandates/` as its standing instructions.
6. Make a room and add the four seats. Send each seat one message by its @handle and wait
   for the reply. A seat that does not answer now will not answer during the run.
7. Replace `<WORKSPACE>` in `dispatch/stage-1.md` with the absolute path of the workspace
   and send the result to the coordinator with `run/room.py send`. Send nothing else until
   its final report.

A seat can also be made in BAND Desktop as a local agent and given its file from `mandates/`
by hand. Everything else stays the same.

The tools need no packages. Their commands are in `tools/README.md`, and the format of a
check list entry is in `tools/CHECKS.md`.

## How a stage runs

1. The coordinator sends the verifier the written requirements. The verifier writes one
   check list entry per sentence before any code exists.
2. The coordinator sends the implementer the whole task, requirements pasted in full.
3. The implementer commits and posts a handoff card: the revision, every command it ran
   with the exit code, and every test file it changed.
4. The falsifier checks everything since the last revision it accepted, on its own copy,
   and posts ACCEPT or REJECT. The tool writes every verdict into a record in the
   falsifier's folder and takes the base of the next check from it.
5. The verifier builds the accepted revision, starts it where only it can reach it, runs the
   check list against the written requirements, and posts the coverage line and one card
   per failing entry. The tool asks for the falsifier's verdict card and refuses a revision
   that was not accepted.
6. Each card goes to the implementer in full. A fix is a new revision, which goes through
   the falsifier again. The verifier then replays the card. A card whose check misread the
   requirements is withdrawn with its reason, and stays in the evidence.
7. The checking seats store their check list and cards in `evidence/`, each committing
   under its own seat name.
8. The coordinator posts the final report and stops.

## How it catches and recovers from bad work

| What goes wrong | Seat | How it is caught | What happens next |
|---|---|---|---|
| Tests that pass without the code they are about | falsifier | runs the delivered tests on the base, which is the last revision it accepted, and on the revision with every non-test change undone; both runs must fail | REJECT, "passes without the implementation" |
| Tests that only check that the code can be called | falsifier | runs the delivered tests on the revision with every Python function the work changed emptied; the run must fail | REJECT, "passes with the code emptied" |
| A base that would forgive rejected work | falsifier | the tool keeps a record of its verdicts and takes the base from it; another base, a rejected revision or one built on a rejected revision is refused | exit 2, nothing is judged until the right base is given |
| A test of an earlier stage changed in its own folder while a later stage is built | falsifier | names every file the work changed outside the stage folder; a changed or deleted test there is a finding | REJECT naming the file |
| A test deleted, skipped or stripped of assertions, in any commit since the base | falsifier | compares every test file with the base | REJECT naming the file |
| A test switched off some other way: renamed, ignored by configuration, marked as not a test | falsifier | compares the tests each changed file declares with the base, and reads the runner's own summary on the revision and on the base as it was | REJECT naming the test, or with both counts |
| A changed test the handoff did not list | falsifier | compares the card with the revision | REJECT naming the file |
| An earlier stage's tests quietly rewritten | falsifier | compares with the revision accepted for that stage | REJECT unless a note names the requirement that changed |
| A claim that does not reproduce | falsifier | re-runs the card's commands on a fresh copy | REJECT with the exit codes |
| A requirement no supplied check asks for | verifier | one entry per sentence of the written requirements | a card quoting the sentence |
| A check list that quotes what no requirement says, or leaves requirements out | verifier | every quoted sentence is looked up in the requirements files; the coverage line counts the requirement lines no entry quotes | exit 2 naming the entry; the count is posted with the coverage line |
| A finding that misread the requirements | verifier, on the implementer's objection | the verifier reads the sentence again | the card is withdrawn with its reason and counted as withdrawn; it is never deleted |
| A revision checked before it was accepted | verifier | the check tool asks for the falsifier's verdict card | exit 2 unless the card is an ACCEPT |
| A card typed by a seat instead of printed by a tool | the tools, before submitting | every posted card is looked for in an earlier tool result of the same seat | the cost table says how many cards trace, and lists the ones that do not |
| A double allocation or lost update under load | verifier | requests released together, then the state read back through the public interface; a burst must pass three times | a card holding the exact burst |
| A response that says one thing while the state says another | verifier | the read-back after every burst | a card with both values |
| A service that is down | the tools | exit 3; findings made before it went down keep their cards | the coordinator is told; an error is never turned into a pass or a finding |
| A check list that checks nothing | the tools | an entry with no expectation, an unknown field or a list with nothing to run is refused with exit 2 | the verifier repairs the list before anything is judged |
| A fix loop that never ends | coordinator | three attempts per finding; three rejections for one reason | the finding is reported as open and work goes on; the three rejections end the stage |

Recovery is always the same path: card, new revision, falsifier, replay. A summary or a
passing unit test never closes a card.

Measured on the tools before the run, against a practice counter written by hand and never
submitted: a build with a deliberate race gave most of 20 increments released together
the same value (17 to 20 of them, by run). The burst entry failed in every round that was
run and wrote a card each time. Each card replayed clean against the corrected build.
The revision check was measured on a practice repository in the layout of the event:
honest work was accepted in a first stage folder and in one copied forward, a function
whose test only checks that it can be called and a test file deleted two commits back
were rejected, and a base that was not the last accepted revision was refused.

## Design choices and what they cost

| Choice | Why | Cost |
|---|---|---|
| A checking seat on a second model family | a seat that checks with the model that built shares its blind spots | one more runtime to stand up, and it did not survive the run: the verifier was tried on an open-weights model (MiniMax-M2.5 through OpenCode), which passed its runtime test and answered in the practice room but could not finish a single turn on a full-size stage task in an hour, so the seat was moved back to Claude Code. The blind-spot argument stands; this second family did not. All four seats in the submitted run are Claude Code on claude-sonnet-5, as `mandates/` says |
| Two of four seats only check | the builder's own tests are the weakest evidence, and most of each stage's checks are held back | every revision is handled by three seats; minutes per stage are in the table below |
| The falsifier works on its own copy | a result pasted by the builder is a claim | one fresh clone and up to four test runs per revision |
| The falsifier judges everything since the last revision it accepted | the builder commits often, and a deleted test can hide behind a later commit or behind a rejected one | the first handoff needs the starting revision on the card; one more test run per check |
| The tool keeps the record of verdicts, not the seat | a seat forgets, and a wrong base forgives what was rejected | one file in the falsifier's folder; whoever can write that file is believed |
| The delivered tests are run on emptied code | in a stage folder that is new, the code is absent on the base, so the older runs fail whatever the tests assert; there the delivered tests also run once on the earlier stage's accepted code | up to two more test runs per check; Python only; a fix whose test asks that nothing happens is accepted with a note only when that test itself failed, by its own name, on code without the work |
| The check list is measured against the requirements files | a list measured against itself is always complete | sentences have to be quoted word for word, and one that is not found stops the run |
| The verifier never learns the source, by order of work and not by force | the room cannot enforce who reads what | see Limits |
| The check list quotes requirement sentences | a list built from the supplied checks would repeat their gaps | the verifier spends time before any code exists; sentences it cannot express stay listed as unchecked |
| A burst must pass three times | a race can pass once by luck | three times the burst time per entry |
| Cards with fixed fields, pasted in full | a seat sees only what is addressed to it | long messages |
| Tools use the standard library only | a seat's sandbox may have no packages and no network | no browser automation, so an element drawn by script is not checked |
| Limits on fix attempts | an unattended run has to end | a finding can stay open |

## What it cost

| Stage | Minutes | Handoffs | Rejections | Findings opened | Findings closed by replay | Model spend |
|---|---|---|---|---|---|---|
| 1 | 173 | 6 | 0 | 5 | 3 | [add from the account] |
| 2 | 1157 (233 without the overnight stall) | 3 | 2 | 1 | 1 | [add from the account] |
| 3 | 73 | 2 | 0 | 12 | 1 | [add from the account] |
| 4 | 100 | 1 | 0 | 22 | 0 | [add from the account] |

[FILL FROM THE RUN: the table per seat that `tools/preflight.py` prints]

Minutes are wall-clock gaps read from the room download, each given to the seat that
posted next. They are not billed compute. Model spend is not in the room; it comes from
the provider's usage page. Delete the rows for stages that were not reached.

## What the run caught

Two rejections, both in stage 2, and five findings the verifier opened and then closed on replay.

**The rejections.** Both came from the same check: a test frozen by an earlier accepted stage may
not change unless a note names the requirement that forced it.

- `0eed3696` (2026-10-04 15:39 UTC) — REJECT, *frozen test changed without a spec-change note*.
  The body of `test_concurrent_identical_requests_commit_exactly_once` in
  `stage-2/tests/test_idempotency.py` was not the body accepted for stage 1, and nothing in
  `stage-2.md` or the design brief asked for the change. The suite itself was green: a plain
  `python3 -m pytest -q` on the posted revision exited 0, so only the comparison with the frozen
  stage-1 test showed it. The implementer restored the accepted body and `f4668cdc` was accepted
  at 16:04 UTC.
- `7737dd4b` (16:33 UTC) — REJECT on the same check, and this time the change was justified.
  Stage 2 makes `table_ids` a list, which is unhashable, so stage 1's
  `tuple(sorted(r.body.items()))` could not dedupe the concurrent responses; the test compared
  `json.dumps(r.body, sort_keys=True)` instead, with the same intent. The implementer gave that
  as a spec-change note, the falsifier re-ran the check with the note, and **the same revision
  `7737dd4b` was accepted** at 17:02 UTC. A frozen test may change. It may not change silently.

**The findings.** Each was raised against a revision, replayed after the fix, and closed only when
every expectation in the replay held.

| check id | what it said | found on | closed |
|---|---|---|---|
| `S1-1.1b-restaurant-scope` | a table id in one restaurant collided with the same id in another: the second booking got 409 `table_unavailable` where 201 was due | `706e5085` | 2026-10-04 12:57 UTC |
| `S1-8.POST-reservations-reference-shape-on-reset` | a seeded reference outside 6-12 characters of A-Z0-9 was accepted: reset answered 204 where 422 was due | `706e5085` | 2026-10-04 12:57 UTC |
| `S1-4.2-opening-hours-no-midnight-cross` | opening hours that cross midnight were accepted: reset answered 204 where 422 was due | `933db32b` | 2026-10-04 13:13 UTC |
| `BROWSER-PRIMARY-BUTTON` | two filled buttons on one screen (`search-button`, `booking-submit`) against the design brief's one | `7737dd4b` | 2026-10-05 08:30 UTC |
| `S3-POL.39a` | a stale revision number answered 422 `validation_failed` where 409 `stale_revision` was due: precedence between the two checks was the wrong way round | `e6f95ec7` | 2026-10-05 09:41 UTC |

Thirty-five further cards were opened and then withdrawn by the verifier itself, most of them in
stage 4, when a replay showed the fault was in its own `series_setup` test helper rather than in
the service. The withdrawal reason is on each card.

Faults planted or staged to make a check fire: **none**. Nothing was broken on purpose to give a checking seat something to find. Every rejection and every finding above came out of the written requirements against the code the implementer actually wrote.

## What failed

### What the practice run showed

The factory ran once on the organizers' unscored practice counter, on 2026-10-03, before the
run that is submitted. Four seats, one room, one human message, and one more after the
machine restarted and every seat lost its session. What went wrong, and what was changed
before the real run:

- **The first rejection was wrong.** The handoff card listed `cd stage-1 && npm test`. The
  revision check already runs a command inside the stage folder, so the command failed on
  its first word and the honest revision was rejected. The tool now refuses such a command
  and says how to write it, and the seat files and the dispatches say that a command is
  written as it is run from inside the stage folder.
- **The second rejection was a run stopped for time.** It printed nothing for 900 seconds.
  The same check passed in two seconds a few minutes later. A test command can wait on the
  standard input a seat's shell hands down; the tool now gives it none. The falsifier runs
  a check once more when its only finding is a run that was stopped for time.
- **The falsifier never posted its verdict as a card.** The supplied checks could not run,
  and it took that as a reason to hold the card back, so the coordinator read the verdict
  from the falsifier's record instead. The card is now posted as soon as the tool has given
  its verdict, and the supplied checks are reported beside it.
- **The organizers' checks could not run at all.** Every attempt failed to publish the
  service's port, eleven times across two seats, while a plain `docker run -p` worked. The
  relay that Docker Desktop keeps between Windows and Linux was wedged, and nothing a seat
  can do reaches it. Restarting Docker Desktop cleared it, and both modes have worked since.
  The dispatches now give the command in isolated mode, which is the mode the entry is
  graded in, and a seat that cannot run it reports that beside its verdict instead of
  holding the verdict back.
- **The machine ran out of memory.** It restarted three times that day under four seats,
  a container build and other work, and each restart killed every seat. Seats that were
  started again picked up the messages they had missed, without a human.
- **What worked without help:** the verifier wrote 23 entries from the requirements before
  any code existed; the coordinator settled the two rejections between the seats and asked
  the human for nothing; the verifier built the accepted revision itself and found nothing.

### Before the run

These happened while the tools were built and tested, before any seat ran:

- **Treating a service that is down as a failed check.** A draft of the check tool, before
  its first commit, answered an unreachable service with a finding. It stops with exit 3.
- **Check ideas that were in no requirement.** An early plan held scenarios nobody had
  asked for. Every entry now has to quote its sentence.
- **Comparing a revision with its parent commit only.** An independent review of the tools,
  done by an AI reviewer that had not seen them being built, showed that a test deleted
  two commits back went unseen, and the revision was accepted.
  The check now covers everything since the last accepted revision.
- **Fixes that broke what worked.** A second review by an AI reviewer, of the fixes only,
  found that the new time limit crashed on every service that closes the connection after
  an answer, that opening connections before a burst made a correct service with few
  workers look broken, and that the rule about skipped tests rejected correct work that
  merely printed the word. A third review found a test renamed away behind new tests, and
  a long answer whose tokens were no longer masked. A fourth found fixtures counted as
  tests. The fifth and last found that the fourth round had deleted a class the tool still
  used, so that an error inside a run ended in a crash, and had narrowed the files that
  are read for test names too far. All of these were fixed and have tests.
- **Wrong verdicts in the layout the event prescribes.** Ten AI judges that had not seen
  the tools being built examined the kit on 2026-09-28. The revision check compared frozen
  tests byte for byte, so it rejected honest work in a stage folder that was copied
  forward and widened. It accepted a wrong function whose test only checked that it could
  be called, because in a new stage folder the older runs fail for want of code, whatever
  the tests assert. It left the base to the seat's memory. The check list was measured
  against itself. The room reader did not read the form BAND Desktop downloads.
  All three tools were repaired, with tests written first.
- **Repairs that were wrong again.** Two AI reviewers examined the repaired revision
  check. The run on emptied code rejected an honest fix whose test asks that nothing
  happens. A summary with a negative duration, which the test runner prints on a machine
  whose clock steps back, was not read, so about one check in fifty of honest work was
  rejected. A frozen test defined a second time under its name was not seen. Tests of
  an earlier stage could be deleted in their own folder. Each has a test now.
- **A repair that opened a door.** A third reviewer examined the repaired repairs and
  bought the new forgiveness with a one-line decoy: any delivered test that failed on the
  bare code excused every other, so a wrong function behind a test that cannot tell was
  accepted again. The card could also name a test as failed that had not failed, because
  a test's name was matched inside another file's path, and the honest fix stayed
  rejected inside a stage folder. A fourth round ties the forgiveness to the test itself,
  which must have failed by its own name on code without the work, reads failed tests'
  names from where a test stands instead of anywhere in a line, and runs the delivered
  tests once on the earlier stage's accepted code when the base holds no stage folder.
- **The last door: what a test prints at shutdown.** The fifth reviewer registered a
  shutdown hook in a test file that printed a forged summary section after the runner's
  closing line, where the tool read it as the runner's own, and bought the forgiveness
  once more; it also showed an honest delivery rejected when its command runs the tests
  from the repository root. The final round reads a pytest log only up to the runner's
  last closing line and matches the vouching file in the two exact forms a runner writes.
  Five fresh reviews is where this chain stopped, by the review rule; what no round could
  remove is written under Limits.
- **A rule gamed through its own key.** A fourth reviewer defeated that round twice: a
  genuine test vouched for a fake test of the same name in another file, and a failure
  block printed by a test was read as the runner's and granted a vouching. A fifth round
  tied the vouching to the file the runner names and let printed blocks only take a
  vouching away. The same reviewer showed that a passing invariant test delivered beside
  a does-nothing fix now draws a rejection; that stays, strict on purpose, and the card
  says what to do instead.
- **Cards that held session tokens.** The same independent review found that a finding card kept the
  sign-in answer and the header that carried the token, and cards are committed. Saved
  values are now written back as placeholders and credential shapes are masked.
- **A check list that checked nothing.** A step with a misspelt expectation passed against
  a service that answered every request with an error. Such a list is now refused.
- **A design brief nobody had looked at.** Three AI evaluators that had not seen the
  design were shown stills of a mock, one after another. They found two buttons that both seemed to book, "Taken" on tables that were only too
  small, and messages too pale to notice. The brief in `dispatch/stage-2.md` was rewritten.

In the run:

- **The second model family did not survive.** The verifier was meant to check on a different
  model from the one that built. It was set up on MiniMax-M2.5 through OpenCode, passed its
  runtime test and answered in the practice room, but could not finish a single turn on a
  full-size stage task in an hour. The seat was moved back to Claude Code before the submitted
  run, so all four seats are Claude Code on claude-sonnet-5.
- **The room went quiet twice, and both times a human had to restart the seats.** A seat finished
  a turn whose message never reached the room, so everyone waited on it: 88 minutes on 4 October
  and fifteen hours overnight into 5 October. The two process notes in the room are those
  restarts. Nothing was lost - each seat's work was already on disk in its own folder, and the
  notes asked the seat to report it rather than redo it - but the factory cannot yet notice its
  own silence from inside. `run/` now carries a watchdog that restarts a seat when the room has
  had no message for thirty minutes.
- **The verifier spent a stage arguing with its own tooling.** All twenty-two stage-4 cards were
  withdrawn once it traced the failures to a conditional save in its own `series_setup` helper
  rather than to the service. It caught that itself, before anyone acted on the cards, which is
  the right failure - but thirty-five of forty findings across the run were withdrawn, and that
  is not free.
- **The checks are API-level, and a browser defect got through.** On every screen the navigation
  shows "Log in" and "Sign up" beside "Log out" while a user is signed in, because
  `renderHeader()` sets the `hidden` attribute on those links while `.nav-link { display: flex }`
  in `app.css` overrides it and no `[hidden] { display: none }` rule exists. The verifier wrote
  itself a browser companion when its own tool could not see a page, and used it to catch two
  filled buttons against the design brief - but it checks the brief's rules, not an HTML
  attribute losing to a CSS declaration. The defect is still in the accepted build, deliberately:
  a stage file a human edits stops counting.
- **The organisers' harness would not run in host mode on this machine.** Every run, by every
  seat, is `--mode isolated`, which is the mode the entry is graded in. The cause was Docker
  Desktop's Windows-to-Linux port relay wedging, not the port range; it cleared after Docker
  Desktop was restarted, and both modes work now.

Earlier runs on the real track, made while the factory was still being built, are not this run
and are not submitted. The practice run on the toy track is described above.

## Limits

- The check list covers what can be said as requests and answers. The coverage line says
  how many sentences that is, and names the rest.
- The verifier builds the image, so the source is on its disk. It is kept from reading it
  by its instructions and by writing the list first. Nothing enforces that, in a sandbox
  or outside one.
- The check list is committed when its stage ends, so from the next stage on the builder
  can read the earlier lists. The list for the stage in hand stays with the verifier.
- The falsifier runs the builder's test command with its own rights. That is the point of
  the check, and it is also why a checking seat belongs in a sandbox when the builder is
  not trusted.
- The falsifier runs the command the handoff card names and trusts its exit code. A
  command that tests nothing is caught only when the work changed tests and code together.
- The count of tests that ran is read from the runner's summary. With a runner the tool
  does not know, or when the two runs do not end alike, nothing is compared, and the
  verdict carries a note that says so. A test hidden by a line in a
  general project file, or behind a condition, shows only in that count, and tests added
  in the same work can make up for it.
- A card masks what is named or shaped like a credential. A credential the service returns
  under a name that does not say so, or inside a page or a script, stays in the card.
- The falsifier believes the output of the test command as it believes its exit code. A
  summary the command makes up is read as a summary.
- The falsifier's "can this test fail" runs apply only when a revision changes tests and
  other files together.
- The run on emptied code empties Python function bodies only. Code at the top of a
  module, in data files, and in every other language stays as it is; for such work the
  run is not made and the verdict says so. One test that notices makes the run fail,
  whatever the other tests do: the verdict names the delivered tests that did not fail,
  and that is a note, not a finding. A test that fails on emptied code need not test
  behaviour.
- A delivered test that passes on emptied code is forgiven only for itself: it must have
  failed, by its own name and in its own file, on code without the work. A test that
  failed beside it buys it nothing, and neither does a test of the same name in another
  file. The names of failed tests are read from pytest, unittest, node and go output;
  with another runner, nothing is forgiven. For pytest only the runner's own summary
  grants a forgiveness, read up to the runner's last closing line; a failure block a test
  prints can only take one away. A made-up unittest block, or a closing line forged after
  the real one, is believed, like a made-up summary.
- On the first handoff into a new stage folder only the run on emptied code can show
  that the delivered tests can fail, and there is no earlier count of tests to compare with.
- The record of verdicts is a plain file. A line typed by hand is believed.
- The check tool takes a verdict card written by hand with the right fields. That a card
  was printed by a tool is checked afterwards, from the room download, not during the run.
- A finding can be withdrawn by the seat that opened it. The reason is kept and counted;
  nobody else has to agree.
- The falsifier counts assertions by pattern. An assertion made weaker without being
  removed is not seen, and a correct rewrite that folds several assertions into one table
  of cases is rejected until the builder adds a note saying why.
- All seats may run the same model, so a checking seat can share the builder's blind spot.
  What differs is what each seat is given.

## Where the tools came from

The files in `tools/` were written before the run by the builder, working with an AI coding
assistant, and tested against a practice counter. The band did not write them. They are
factory equipment, like the seat instruction files.

No file under `stage-1/` to `stage-4/` was written or edited by hand:
`git log --format='%an' -- stage-1 stage-2 stage-3 stage-4` returns `implementer` for all nine
commits that touch a stage folder, and no other author appears there. The seats' commits across
the whole repository are nine by `implementer`, eleven by `verifier` and four by `falsifier`. The
human's are the first commit, which laid down `README.md`, `FACTORY.md`, `mandates/`, `dispatch/`,
`tools/` and `run/` before the band started, and the commits made after the run that filled the
run fields in this file and in `README.md`. None of them touches a stage folder.
`git log --merges` is empty and the reflog holds no rebase: the history is as the seats made it.

## Point it at another problem

1. Keep `mandates/` and `tools/` as they are.
2. Write your own dispatch files. `dispatch/stage-1.md` shows what one names: the
   workspace, the result repository, the written requirements and the stage folder.
3. The verifier writes a new check list from your requirements, in the format
   `tools/CHECKS.md` describes.
4. If what you build is not a service that answers requests, `tools/speccheck.py` does not
   apply. The cards, `tools/falsify.py` and `tools/preflight.py` still do.
