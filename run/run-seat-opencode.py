#!/usr/bin/env python3
"""Run one seat of the factory on an open-weights model, through OpenCode.

A checking seat on a second model family does not share the builder's blind spots. The seat is the
same in every other way: the same mandate file, the same working folder, the same room.

usage: python run-seat-opencode.py <seat> <working folder> <mandate file> [--model PROVIDER/MODEL]
       [--port 4096]
needs: the OpenCode server, started beside it and left running:
         FEATHERLESS_API_KEY=... opencode serve --hostname=127.0.0.1 --port=4096
       a provider in ~/.config/opencode/opencode.json (never in a repository: a key written in the
       working folder would be committed), and `pip install 'band-sdk[opencode]'`
       BAND_WORKSPACE names the workspace; left out, it is three folders above this file
"""
import asyncio
import os
import pathlib
import sys

from band import Agent
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.config import load_agent_config
from band.core.types import Emit

WORKSPACE = pathlib.Path(os.environ.get("BAND_WORKSPACE") or pathlib.Path(__file__).resolve().parents[3])


def main() -> int:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    seat, cwd, mandate = sys.argv[1], pathlib.Path(sys.argv[2]).resolve(), pathlib.Path(sys.argv[3]).resolve()
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "featherless/MiniMaxAI/MiniMax-M2.5"
    port = sys.argv[sys.argv.index("--port") + 1] if "--port" in sys.argv else "4096"
    provider, _, model_id = model.partition("/")
    if not cwd.is_dir():
        sys.exit(f"{cwd} is not a folder")
    agent_id, api_key = load_agent_config(seat, config_path=WORKSPACE / "agent_config.yaml")
    adapter = OpencodeAdapter(
        config=OpencodeAdapterConfig(
            base_url=f"http://127.0.0.1:{port}",
            # absolute, and the same folder the seat is given in its task
            directory=str(cwd),
            provider_id=provider,
            model_id=model_id,
            custom_section=mandate.read_text(encoding="utf-8"),
            # an unattended run: the default waits for a human on every tool call
            approval_mode="auto_accept",
            turn_timeout_s=1800,
        ),
        emit=Emit.TOOL_CALLS,
    )
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
    print(f"seat {seat} up on {model}: cwd={cwd}", flush=True)
    asyncio.run(agent.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
