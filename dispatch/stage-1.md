You are the coordinator of this factory. This message is the only human input for stage 1.

Workspace root: <WORKSPACE>
Kickoff checkout (written requirements and supplied checks): <WORKSPACE>/dark-factory-wearedevs
Track: tablekeeper
Result repository: <WORKSPACE>/band-work/result
Implementer's working folder: the result repository
Falsifier's folder: <WORKSPACE>/band-work/falsifier, with its own clone of the result repository in <WORKSPACE>/band-work/falsifier/result
Verifier's folder: <WORKSPACE>/band-work/verifier, with its own clone of the result repository in <WORKSPACE>/band-work/verifier/result
Factory tools: each checking seat uses its own copy of the tools, in the tools folder inside its own folder (commands in tools/README.md). The implementer uses the copy in the result repository, for its cards only.
Supplied checks output: <WORKSPACE>/band-work/checks (every run needs a new output folder)
Stage folder: <WORKSPACE>/band-work/result/stage-1
Written requirements for this stage: <WORKSPACE>/dark-factory-wearedevs/tablekeeper/spec/stage-1.md

Task: implement stage 1 of the track in the stage folder, so that the running service does everything the written requirements say.

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
  number of this stage and the folder of this stage (--stage 1 --subdir stage-1), writes each verdict card into
  <WORKSPACE>/band-work/falsifier/cards/ (--out), and names that file in its verdict.
- The verifier gives the check tool the written requirements and the falsifier's verdict card of the revision it checks,
  which it reads where the falsifier's verdict says the file is:
  --spec "<WORKSPACE>/dark-factory-wearedevs/tablekeeper/spec/stage-1.md" --accepted <the file of the verdict card>
- The base of the first handoff is the last revision the falsifier accepted; in stage 1, where nothing is accepted yet,
  it is the revision the result repository is at when this message arrives.
- The verifier builds and starts the service from its own clone, as the run instructions in the stage folder say,
  published on 127.0.0.1 only, and checks it there. That the service needs no outside network is shown by the
  falsifier's run in isolated mode.
- The falsifier runs the supplied checks, on its own clone at the posted revision:
  cd "<WORKSPACE>/dark-factory-wearedevs" && . .venv/bin/activate && python -m harness run --track tablekeeper --repo "<WORKSPACE>/band-work/falsifier/result" --stage 1 --out "<WORKSPACE>/band-work/checks/s1-<run number>" --mode isolated
  The line that decides is `claimed stage: 1`. The run also prints one line for stage 2, whose checks it tries against this folder. That line,
  `stage 2: fail` or `stage 2: error`, is expected: a folder that passes the next stage's checks claims nothing.
  The command runs in isolated mode, the mode the entry is graded in: the service is built, started with no outbound
  network and checked there. No seat runs the supplied checks in another mode.
- The falsifier posts the card of the revision check as soon as the tool has given its verdict. What the supplied
  checks said, or that they could not run, is reported beside it and does not hold the card back.
- When the stage is finished, the verifier stores this stage's check list and cards in
  <WORKSPACE>/band-work/result/evidence/stage-1/ and the falsifier stores its verdict cards there. Each commits only
  those files.
- Post the final report, then stop. Do not start the next stage.
