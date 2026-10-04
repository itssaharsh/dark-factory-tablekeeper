# Dispatches

One message per stage, sent to the coordinator and to nobody else. In the run you submit, each dispatch is
the only human input for its stage: send nothing else until the coordinator's final report, and never send
the same stage twice.

1. Replace `<WORKSPACE>` with the absolute path of the folder that holds `dark-factory-wearedevs/` and `band-work/`:
   `sed "s#<WORKSPACE>#/absolute/path#g" dispatch/stage-1.md`
2. Paste the result into the room, addressed to the coordinator.
3. Wait for the final report. Then send the next stage, or stop.

These files name the track. That is where track detail belongs; the seat instruction files in `mandates/` name none.
Stage 2 carries the design brief for the browser product.

Each dispatch expects what the runbook sets up: a virtual environment in the kickoff checkout, a clone of the
result repository inside each checking seat's folder, and a copy of `tools/` beside each clone.
