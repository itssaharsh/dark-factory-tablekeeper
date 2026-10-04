# How the seats run

The four seats are Band agents that run as processes on the builder's machine. Each one is Claude
Code, connected to the room through the `claude_sdk` adapter of `band-sdk`, with its file from
`mandates/` as its standing instructions. Band Desktop and the Band console show the same rooms.

| File | What it does |
|---|---|
| `run-seat.py` | runs one seat: its identity from `agent_config.yaml`, its working folder, its mandate file |
| `seats.sh` | starts, stops and lists the four seats of a run; logs go to `seat-logs/` in the workspace |
| `run-seat-opencode.py` | runs one seat on an open-weights model through OpenCode, for a second model family |
| `room.py` | the human's side: registers the seats, makes a room, seats the agents, sends the dispatch, reads the room |

## Stand it up

The workspace is the folder that holds `dark-factory-wearedevs/` and `band-work/`. Two files live
in it and in no repository: `band.env` with the human's Band key, and `agent_config.yaml`, which
`room.py register` writes.

```sh
cd <WORKSPACE>/dark-factory-wearedevs && . .venv/bin/activate
pip install 'band-sdk[claude_sdk]' 'mcp<2' pyyaml
cd <WORKSPACE>/band-work/result
python run/room.py register coordinator "Plans each stage, routes every handoff, writes the final report. Writes no code."
python run/room.py register implementer "Writes the product code and its own tests in the stage folder."
python run/room.py register falsifier   "Re-runs every committed revision on its own clone and gives the verdict."
python run/room.py register verifier    "Checks the running service against the written requirements."
run/seats.sh start real
ROOM=$(python run/room.py create "Stage 1")
python run/room.py add "$ROOM" coordinator implementer falsifier verifier
python run/room.py send "$ROOM" <the dispatch of the stage, as a file> --to coordinator
```

`seats.sh start` gives the implementer and the coordinator the result repository as their working
folder, and each checking seat its own folder with its own clone and its own tools. A seat that is
started again picks up the messages it missed.

## A checking seat on a second model family

A seat that checks with the model that built shares its blind spots. One checking seat can run on an
open-weights model instead, through OpenCode, with the same mandate and the same folder.

```sh
pip install 'band-sdk[opencode]'
curl -fsSL https://opencode.ai/install | bash
# the provider goes in ~/.config/opencode/opencode.json, never in the repository: a key written in
# the working folder would be committed. See the organizers' guide for the file's shape.
FEATHERLESS_API_KEY=... opencode serve --hostname=127.0.0.1 --port=4096 &
BAND_OPENCODE_SEATS=verifier BAND_OPENCODE_MODEL=featherless/MiniMaxAI/MiniMax-M2.5 run/seats.sh start real
```

Prove the model writes files before you give it a seat:
`opencode run -m <provider>/<model> "Create hello.txt containing the word banana, then run 'wc -c hello.txt'."`
If the seat will not start, run it on Claude Code and say so in the report. A run is not the place to
debug a runtime.

## What it cannot do

- It cannot download the room. Open the room in the Band console and choose Download full session.
- A seat runs with every permission in its working folder, because an unattended run cannot wait
  for approval. Run it on a machine you can afford to lose, or in a sandbox.
- Four seats, a container build and the supplied checks need about 4 GB of free memory. On a
  machine that runs short, seats die mid-turn and the room shows it.
