#!/usr/bin/env python3
"""Run one seat of the factory as a Band agent, on Claude Code through the band-sdk claude_sdk adapter.

The seat's identity (agent id and key) comes from agent_config.yaml in the workspace, never from a
repository. The seat's standing instructions are its file in mandates/, the same text that is committed.

usage: python run-seat.py <seat> <working folder> <mandate file> [--model NAME]
needs: pip install 'band-sdk[claude_sdk]' 'mcp<2', and Claude Code signed in on this machine
       BAND_WORKSPACE names the workspace; left out, it is three folders above this file
"""
import asyncio
import os
import pathlib
import sys

from band import Agent
from band.adapters.claude_sdk import ClaudeSDKAdapter, ClaudeSDKAdapterConfig
from band.config import load_agent_config
from band.core.types import Emit

WORKSPACE = pathlib.Path(os.environ.get("BAND_WORKSPACE") or pathlib.Path(__file__).resolve().parents[3])


def main() -> int:
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    seat, cwd, mandate = sys.argv[1], pathlib.Path(sys.argv[2]).resolve(), pathlib.Path(sys.argv[3]).resolve()
    model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "claude-sonnet-5"
    if not cwd.is_dir():
        sys.exit(f"{cwd} is not a folder")
    agent_id, api_key = load_agent_config(seat, config_path=WORKSPACE / "agent_config.yaml")
    adapter = ClaudeSDKAdapter(
        config=ClaudeSDKAdapterConfig(
            model=model,
            custom_section=mandate.read_text(encoding="utf-8"),
            # An unattended run: a seat that waits for approval stops the factory. A seat holds only its
            # own working folder, and the checking tools work on temporary copies.
            permission_mode="bypassPermissions",
            cwd=str(cwd),
            # A test suite or a container build has to fit in one turn.
            turn_timeout_s=1800,
        ),
        emit=Emit.TOOL_CALLS | Emit.THOUGHTS,
    )
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
    print(f"seat {seat} up: cwd={cwd} model={model}", flush=True)
    asyncio.run(agent.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
