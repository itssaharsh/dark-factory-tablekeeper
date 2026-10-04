#!/usr/bin/env python3
"""Drive a Band room as the human: register the seats, make a room, seat the agents, send the one
message of a stage, read what was said.

usage: python room.py register <seat> "<what the seat does, 10 to 500 characters>"
       python room.py create "<title>"                      prints the id of the new room
       python room.py add <room id> <seat> [<seat> ...]
       python room.py send <room id> <file or text> [--to seat[,seat...]]
       python room.py read <room id> [--limit N]            newest first, at most 100
       python room.py dump <room id> <out.jsonl> [--type text|all|tool_call|...]
The human's Band key is read from band.env in the workspace (a line that starts with BAND_USER_API_KEY).
The seats' ids and keys are kept in agent_config.yaml in the workspace. Neither file is in a repository.
BAND_WORKSPACE names the workspace; left out, it is three folders above this file.
A message sent here is a human message in the room. A stage gets one: its dispatch.
"""
import asyncio
import os
import pathlib
import sys

import yaml
from band_rest.client import AsyncRestClient
from band_rest.human_api_chats.types.create_my_chat_room_request_chat import CreateMyChatRoomRequestChat
from band_rest.types.agent_register_request import AgentRegisterRequest
from band_rest.types.chat_message_request import ChatMessageRequest
from band_rest.types.chat_message_request_mentions_item import ChatMessageRequestMentionsItem
from band_rest.types.participant_request import ParticipantRequest

WORKSPACE = pathlib.Path(os.environ.get("BAND_WORKSPACE") or pathlib.Path(__file__).resolve().parents[3])
SEATS_FILE = WORKSPACE / "agent_config.yaml"
NAME = "BAND_USER_API_" + "KEY"


def human() -> str:
    for line in (WORKSPACE / "band.env").read_text().splitlines():
        if line.startswith(NAME):
            return line.partition("=")[2].strip()
    sys.exit(f"band.env in {WORKSPACE} holds no line for {NAME}")


def seats() -> dict:
    return yaml.safe_load(SEATS_FILE.read_text()) if SEATS_FILE.exists() else {}


async def main() -> int:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    client = AsyncRestClient(api_key=human(), base_url=os.environ.get("BAND_REST_URL", "https://app.band.ai"))
    word = sys.argv[1]
    if word == "register":
        seat, known = sys.argv[2], seats()
        if seat in known:
            sys.exit(f"{seat} is in {SEATS_FILE} already")
        got = (await client.human_api_agents.register_my_agent(
            agent=AgentRegisterRequest(name=seat, description=sys.argv[3]))).data
        known[seat] = {"agent_id": str(got.agent.id), "api_key": str(got.credentials.api_key)}
        SEATS_FILE.write_text(yaml.safe_dump(known))
        SEATS_FILE.chmod(0o600)
        print(f"registered {seat} as {got.agent.id}; its key is in {SEATS_FILE}")
    elif word == "create":
        got = await client.human_api_chats.create_my_chat_room(chat=CreateMyChatRoomRequestChat(title=sys.argv[2]))
        print(got.data.id)
    elif word == "add":
        known = seats()
        for seat in sys.argv[3:]:
            await client.human_api_participants.add_my_chat_participant(
                sys.argv[2], participant=ParticipantRequest(participant_id=known[seat]["agent_id"], role="member"))
            print("added", seat)
    elif word == "send":
        source = sys.argv[3]
        try:
            text = pathlib.Path(source).read_text() if pathlib.Path(source).is_file() else source
        except OSError:                             # a long text is no file name
            text = source
        known, mentions = seats(), []
        if "--to" in sys.argv:
            for seat in sys.argv[sys.argv.index("--to") + 1].split(","):
                mentions.append(ChatMessageRequestMentionsItem(id=known[seat]["agent_id"], handle=seat, name=seat,
                                                               kind="mention"))
        got = await client.human_api_messages.send_my_chat_message(
            sys.argv[2], message=ChatMessageRequest(content=text, mentions=mentions))
        print("sent", getattr(got.data, "id", ""))
    elif word == "read":
        limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 20
        got = await client.human_api_messages.list_my_chat_messages(sys.argv[2], limit=min(limit, 100))
        for m in got.data or []:
            body = (m.content or "").strip().replace("\n", " ")
            print(f"[{m.message_type}] {m.sender_name or m.sender_id}: {body[:300]}")
    elif word == "dump":
        out = pathlib.Path(sys.argv[3])
        kind = sys.argv[sys.argv.index("--type") + 1] if "--type" in sys.argv else "text"
        rows, page = [], 1
        while True:
            got = await client.human_api_messages.list_my_chat_messages(
                sys.argv[2], page=page, page_size=100, message_type=None if kind == "all" else kind)
            batch = got.data or []
            rows += batch
            if len(batch) < 100:
                break
            page += 1
        rows.sort(key=lambda m: str(m.inserted_at or ""))
        with out.open("w", encoding="utf-8") as f:
            for m in rows:
                f.write(m.model_dump_json() + "\n")
        print(f"{len(rows)} {kind} message(s) written to {out}")
    else:
        sys.exit(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
