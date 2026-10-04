# [FILL FROM THE RUN: team name] - Dark Factory entry

| | |
|---|---|
| Event | WeAreDevelopers x BAND, Dark Factory (hackathon edition) |
| Track | tablekeeper |
| Stage reached | [FILL FROM THE RUN: highest stage folder that claims its stage on the supplied checks] |
| Seats | coordinator, implementer, falsifier, verifier |
| Video | [FILL FROM THE RUN: link] |
| Presentation | [FILL FROM THE RUN: link] |

## How the code was made

- [FILL FROM THE RUN: "Every file under the stage folders was written by the four seats in one BAND Desktop room", once the Git authors confirm it]
- [FILL FROM THE RUN: "The human sent one message per stage and nothing else", once `tools/preflight.py` counts one human message per stage folder; otherwise say what else was sent]
- [FILL FROM THE RUN: "The history is as the seats made it: nothing was amended, rebased or squashed", or say what was changed and why]
- The human wrote `README.md`, `FACTORY.md`, the files in `mandates/`, `dispatch/` and `tools/`, and the first commit.

## How to read this repository

| Path | What it holds |
|---|---|
| `FACTORY.md` | the factory: seats, how to stand it up, design choices, what it cost, how bad work is caught |
| `mandates/` | one instruction file per seat, with the harness and model it runs |
| `dispatch/` | the message sent to the coordinator for each stage |
| `tools/` | the programs the checking seats call; commands in `tools/README.md` |
| `stage-1/` to `stage-4/` | one complete service per stage, each with a `Dockerfile` and a `RUN.md`; only the stages that were reached are present |
| `evidence/stage-N/` | [FILL FROM THE RUN: "the verifier's check list and cards, and the falsifier's verdict cards, for that stage", once they were copied into it; otherwise remove this row] |
| `room.json` | the whole room as downloaded from BAND, unchanged |

## Run a stage

Follow the `RUN.md` inside the stage folder. Each folder builds alone and needs no outside
network once it is running. To run the supplied checks from the kickoff checkout:

```sh
python -m harness run --track tablekeeper --repo <this repository> --stage 1 --mode isolated
```

## Follow one piece of bad work through the room

1. Open a card in `evidence/stage-N/`. A finding card quotes the requirement, the exact
   requests, what was expected and what was observed.
2. Search `room.json` for the card's check id. It shows who posted the card, who passed it
   on and which revision answered it.
3. Find that revision in the Git history.
4. Replay the card yourself against the running stage:

```sh
python3 tools/speccheck.py replay evidence/stage-N/<card>.json --base-url http://127.0.0.1:<port>
```

Exit 0 means every expectation in the card holds. A token the check signed in with is
written in the card as a placeholder and filled again when the card is replayed.

## Check the entry offline

```sh
python3 tools/preflight.py . --track tablekeeper --harness <kickoff checkout>
```

It lists gate problems, how the work was spread across the seats, every human message in
the room and the cost table.

## What is not the seats' work

The files in `tools/` were made before the run and are not the seats' work. `FACTORY.md`
says where they came from.
