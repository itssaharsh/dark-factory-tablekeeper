You are the coordinator of this factory. This message is the only human input for stage 2.

Workspace root: <WORKSPACE>
Kickoff checkout (written requirements and supplied checks): <WORKSPACE>/dark-factory-wearedevs
Track: tablekeeper
Result repository: <WORKSPACE>/band-work/result
Implementer's working folder: the result repository
Falsifier's folder: <WORKSPACE>/band-work/falsifier, with its own clone of the result repository in <WORKSPACE>/band-work/falsifier/result
Verifier's folder: <WORKSPACE>/band-work/verifier, with its own clone of the result repository in <WORKSPACE>/band-work/verifier/result
Factory tools: each checking seat uses its own copy of the tools, in the tools folder inside its own folder (commands in tools/README.md). The implementer uses the copy in the result repository, for its cards only.
Supplied checks output: <WORKSPACE>/band-work/checks (every run needs a new output folder)
Stage folder: <WORKSPACE>/band-work/result/stage-2
Written requirements for this stage: <WORKSPACE>/dark-factory-wearedevs/tablekeeper/spec/stage-2.md
Earlier requirements still apply: stage-1.md in the same folder. Read all of them.
Start by copying <WORKSPACE>/band-work/result/stage-1 to stage-2, then widen the copy.
Frozen reference for earlier tests: the revision the falsifier accepted for stage 1.
The service in this folder must also accept an export produced by the earlier stage folders.

Task: implement stage 2 of the track in the stage folder, so that the running service does everything the written requirements for stages 1 to 2 say.

Rules that hold for every stage:
- Build to the written requirements, not to the supplied checks. Only part of each stage's checks were supplied.
- The stage folder holds a build file, run instructions and the source. The service must build and serve from a clean
  container with no access to outside networks at run time. Limits: 2 processors, 2 GiB of memory, 60 seconds to the
  first healthy response, up to 50 requests in flight, 5 seconds per request.
- Every font, script and stylesheet is inside the image.
- This folder solves this stage and not the next one.
- Every handoff carries the complete task and the complete written requirements, pasted.
- Every command on a handoff card is written as it is run from inside the stage folder, and must pass there on a
  fresh clone of the result repository.
- Language: write the service in Python 3.12 with the standard library only, and its tests so that
  `python3 -m pytest -q` runs them from inside the stage folder. The revision check can then empty the code the work
  changed and see whether the tests notice. Pages, styles and scripts for the browser are written as the requirements ask.
- The falsifier keeps the record of its verdicts in <WORKSPACE>/band-work/falsifier/accepted.jsonl and gives it to the
  revision check on every run (--ledger), so that the tool names the base and the frozen tests. It gives the tool the
  number of this stage and the folder of this stage (--stage 2 --subdir stage-2), writes each verdict card into
  <WORKSPACE>/band-work/falsifier/cards/ (--out), and names that file in its verdict.
- The verifier gives the check tool the written requirements and the falsifier's verdict card of the revision it checks,
  which it reads where the falsifier's verdict says the file is:
  --spec "<WORKSPACE>/dark-factory-wearedevs/tablekeeper/spec/stage-1.md" "<WORKSPACE>/dark-factory-wearedevs/tablekeeper/spec/stage-2.md" --accepted <the file of the verdict card>
- The base of the first handoff is the last revision the falsifier accepted; in stage 1, where nothing is accepted yet,
  it is the revision the result repository is at when this message arrives.
- The verifier builds and starts the service from its own clone, as the run instructions in the stage folder say,
  published on 127.0.0.1 only, and checks it there. That the service needs no outside network is shown by the
  falsifier's run in isolated mode.
- The falsifier runs the supplied checks, on its own clone at the posted revision:
  cd "<WORKSPACE>/dark-factory-wearedevs" && . .venv/bin/activate && python -m harness run --track tablekeeper --repo "<WORKSPACE>/band-work/falsifier/result" --stage 2 --out "<WORKSPACE>/band-work/checks/s2-<run number>" --mode isolated
  The line that decides is `claimed stage: 2`. The run also prints one line for stage 3, whose checks it tries against this folder. That line,
  `stage 3: fail` or `stage 3: error`, is expected: a folder that passes the next stage's checks claims nothing.
  The command runs in isolated mode, the mode the entry is graded in: the service is built, started with no outbound
  network and checked there. No seat runs the supplied checks in another mode.
- The falsifier posts the card of the revision check as soon as the tool has given its verdict. What the supplied
  checks said, or that they could not run, is reported beside it and does not hold the card back.
- When the stage is finished, the verifier stores this stage's check list and cards in
  <WORKSPACE>/band-work/result/evidence/stage-2/ and the falsifier stores its verdict cards there. Each commits only
  those files.
- Post the final report, then stop. Do not start the next stage.

Design brief for the browser product (it is part of this task; pass it on in full with the written requirements):

Design direction for the browser product: a host stand. The page is linen (#F6F3EA) with a bottle-green band (#0F3D24) across the top holding the wordmark "Tablekeeper" and the search controls. Every control in the band has the same height, border and corner radius, and none is left in the browser's default style. The Search button is cream (#F6F3EA) with green text, so it reads as ready. The grid sits on a white book page: a header row of table labels with seat counts, then one row per time with the time at the left, one cell per table in fixture order, then the combination cells the specification calls for. Cells share the row's width equally (at least 96 px each), so the grid fills its page. A booking panel sits to the right of the grid at desktop widths. Before a cell is chosen the panel shows one muted line, outside the booking form: "Pick a table to book it.", or "Searching for tables…" while a search runs. Under 768 px the panel sits under the grid until a cell is chosen; then it is placed directly under the chosen time's row and takes focus, so the message, the form and the neighbouring tables are on screen together.
The states must never look alike, and none may rely on colour alone. Available: white cell with a border, table label and seat count. Unavailable has two looks, both with `data-available="false"`. "Taken", for a table that seats the party but is booked: sunken cell with a diagonal pencil hatch, the label in muted ink and the word "Taken". "Too small", for a table whose capacity is below the searched party size: sunken cell with no hatch, the label in muted ink and the words "Too small". Chosen: filled green (#14532D) with a check mark and white text. Loading: skeleton rows that keep the grid's header row and row count so nothing jumps, where each place has no border and no fill and shows two short grey bars with a shimmer, so it looks neither pressable nor unavailable; the skeleton is not the results container and carries none of the specification's test ids; buttons read "Searching…" or "Booking…" without changing width. Booked: a green reserved card at the top of the panel with the word "Reserved", the reference in a monospaced face at 28 px, the restaurant, table, party size and local time, and the link "View or cancel this booking"; the cell turns pale green (#E1EEE5) with a 2 px green border, a check mark and the word "Yours"; the form stays under the card, its summary gains a check mark and the tag "Yours", its button changes from filled to outlined, and a line under the summary reads "This table is booked for you." Refused: a red message (#A3261D on #FBEDEA) at the top of the panel, with a 1 px red border, a 3 px red rule on its left and a warning triangle inline before its first sentence, which is semibold: "That table was just taken. Pick another time or table."; Other refusals use the same red message with words that name their reason. After "just taken" the grid refreshes and the cell that was lost looks taken with a 2 px red outline and the words "Just taken"; the form keeps its values, its summary gains the tag "Taken", and its button stays outlined until another cell is chosen. Unsure: an amber message (#7A4F00 on #FBF1D9) at the top of the panel, built the same way with a question mark in a circle: "We didn't get an answer. Your table may already be booked. Try again: you won't be booked twice."; the chosen cell changes to the amber tint with a 2 px amber border, a question mark in place of the check and "Not confirmed" on one line in place of the seat count; the form's own button reads "Try again" and resends the same key and body. Never show two buttons that book.
Type: Marcellus for the wordmark, restaurant names and "Reserved"; Figtree for everything else; Spline Sans Mono for references only. Bundle the font files in the image at build time (the fontsource packages) and fall back to Georgia, system-ui and ui-monospace; never link a font, script or stylesheet from another host. Body 15 px, inputs 16 px, times in tabular numerals.
Rules: visible labels on every input; a 2 px dark focus outline on every control; targets at least 44 px on touch; no page-level sideways scroll at 375 px; one filled button per screen; no gradients, shadows on cells, emoji, sound or confetti; show table labels and restaurant names, never raw identifiers, as the main text. Every `data-testid`, `data-available` value and status word in the specification stays exactly as written, and the tags added to the summary sit beside its text without replacing the table label or the time.
