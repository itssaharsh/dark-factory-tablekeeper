# Saharsh Tibrewala - Dark Factory entry

| | |
|---|---|
| Event | WeAreDevelopers x BAND, Dark Factory (hackathon edition) |
| Track | tablekeeper |
| Stage reached | **4** - `python -m harness run --track tablekeeper --repo <fresh clone> --all --mode isolated` prints `claimed stage: 4 on the shipped checks` |
| Seats | coordinator, implementer, falsifier, verifier |
| Video | [add the link once uploaded] |
| Presentation | [add the link once uploaded] |

## How the code was made

- Every file under the stage folders was written by the four seats in one BAND Desktop room.
  `git log --format=%an -- stage-1 stage-2 stage-3 stage-4` returns `implementer` for all nine
  commits that touch a stage folder, and for no other author.
- The human sent **six** messages in all: the four stage dispatches, and two process notes, at
  2026-10-04 12:36 UTC and 2026-10-05 08:26 UTC. Each note opens by naming itself as a process
  note and says nothing about the service or what to check. Both were sent after a seat ended a
  turn whose message never reached the room, leaving the others waiting; they say that the seats
  were restarted and ask the seat to report work it had already finished rather than redo it.
- The history is as the seats made it: nothing was amended, rebased or squashed.
  `git log --merges` is empty and the reflog holds no rebase.
- The human wrote `README.md`, `FACTORY.md`, the files in `mandates/`, `dispatch/` and `tools/`, and the first commit.

## How to read this repository

| Path | What it holds |
|---|---|
| `FACTORY.md` | the factory: seats, how to stand it up, design choices, what it cost, how bad work is caught |
| `mandates/` | one instruction file per seat, with the harness and model it runs |
| `dispatch/` | the message sent to the coordinator for each stage |
| `tools/` | the programs the checking seats call; commands in `tools/README.md` |
| `stage-1/` to `stage-4/` | one complete service per stage, each with a `Dockerfile` and a `RUN.md`; only the stages that were reached are present |
| `evidence/stage-N/` | the verifier's check list and cards, and the falsifier's verdict cards with their run logs, for that stage |
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
