#!/usr/bin/env python3
"""Read a result repository the way a judge will, before it is submitted.

Lists gate problems (layout, seat instruction files, the room download, credential
shapes, run fields left unfilled), how the work was spread across the seats, every human message in the room,
how many of the posted cards trace to a tool run, and the cost table that FACTORY.md needs. Nothing is
changed and nothing is sent.

A card traces when a tool result of the seat that posted it, earlier in the room, holds the card's first line
and, below it, the lines that say which card it is and what it decided: for a verdict the verdict and the
revision, for a finding the check line (the check, its kind and the status) and the revision if it names
one, for a handoff the revision. They stand in one text of that result, as whole lines, in the order of a
card and before the first line of any other card. A result does not count when the call it answers was
handed the card. The status of a finding is read from its check line alone. A line that starts like a card
(the word, in capitals or not, and a type within 20 characters behind it) and is not in the form the tools
print is no card: it is listed, and counted nowhere else.

usage: preflight.py <result repository> --track <track>
                    [--harness <kickoff checkout>] [--costs-out costs.json]
exit:  0 ready (warnings may be listed), 1 problems listed, 2 the path is unusable,
       3 this tool failed and nothing was judged
Standard library only.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import subprocess
import sys

ROOM = "room.json"
DOWNLOAD = ("open the room in the Band console, choose Download, then Download full session, "
            "and save the file unchanged as room.json at the repository root")
SEAT_FIELDS = ("Harness", "Model")
STAGE_FOLDER = re.compile(r"^stage-[1-4]$")
# The shapes the organizers' checker looks for. A match names the file, never the value.
SECRETS = (
    ("bearer-token", r"(?i)\bbearer\s+(?=[A-Za-z0-9._\-]*\d)[A-Za-z0-9._\-]{20,}"),
    ("api-key", r"\bsk-[A-Za-z0-9._\-]{16,}"),
    ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    ("env-assignment", r"(?i)\b[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*\S+"),
    ("url-credentials", r"""(?<=://)[^/\s:@"'\\]+:[^/\s@"'\\]+(?=@)"""),
)
CONFIG_ONLY = {"env-assignment"}
CONFIG_SUFFIXES = {".env", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".json"}
TEXT_SUFFIXES = CONFIG_SUFFIXES | {".md", ".py", ".txt", ".jsonl", ".js", ".ts", ".sh", ".go", ".rb",
                                   ".rs", ".java"}
TEXT_NAMES = {"Dockerfile", ".env", ".envrc", "Makefile"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "target", "dist"}
TIME_KEYS = ("createdAt", "created_at", "timestamp", "sentAt", "sent_at", "insertedAt", "inserted_at",
             "time", "ts")
NOT_HUMAN = {"agent", "system", "bot", "tool"}
CARD = re.compile(r"CARD (handoff|verdict|finding) \| stage (\d+) \| (\S+) -> \S+")
# A line that starts like a card, behind mentions and marks, whatever form the rest of it has: the word in
# capitals or not, and a type within 20 characters behind it. The name of the card tool is not the word.
CARD_LIKE = re.compile(r"^(?:[\s>*_#`~-]|@\[\[[^\]]*\]\])*(?i:CARD)\b(?!\.\w)"
                       r".{0,20}?\b(?i:handoff|verdict|finding)\b")
MENTIONS = re.compile(r"^(?:\s|@\[\[[^\]]*\]\])+")
# The lines that say which card it is and what it decided, in the order a card holds them. With the first line
# they are what a card is traced by: every verdict of a stage has the same first line and the same two words.
LINE = {"verdict": re.compile(r"(?m)^[ \t]*verdict: .*$"),
        "check": re.compile(r"(?m)^[ \t]*check: .*$"),
        "revision": re.compile(r"(?m)^[ \t]*revision: .*$")}
DECIDES = {"handoff": ("revision",), "verdict": ("verdict", "revision"), "finding": ("check", "revision")}
# A finding names a revision only when its check was given one.
MAY_LACK = {("finding", "revision")}
# What became of a finding stands on its check line. Where the line holds the words twice, the first decide.
STATUS = re.compile(r"\bstatus: (open|closed|withdrawn)\b")
# A seat joining or leaving is an event of the room, not work of a stage.
ROOM_EVENTS = {"participant"}
TOOL_CALL, TOOL_RESULT = "tool_call", "tool_result"
# A download keeps the start of a long tool result and ends it with this character.
CUT = "…"
MAX_OPENED = 50
# A value that follows a telling name is never printed, whatever its shape.
NAMED = re.compile(r"(?i)((?:password|passwd|secret|token|api[_-]?key|credential)s?\b\W{0,3})(\S+)")
LOPSIDED = 0.80
# A figure that must come from the submitted run is written as this marker until it is filled.
UNFILLED = "[FILL FROM THE RUN"
MIN_MESSAGES_FOR_SPREAD = 5
COLUMNS = ("stage", "seat", "minutes", "messages", "handoffs", "rejections", "findings opened",
           "findings closed", "findings withdrawn", "cards traced")


def slug(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def layout(root: pathlib.Path) -> list[str]:
    problems = [f"{name} is missing; it is where the factory is described"
                for name in ("README.md", "FACTORY.md") if not (root / name).is_file()]
    if not (root / "stage-1").is_dir():
        problems.append("stage-1/ is missing; every entry needs one complete stage 1")
    for child in sorted(p for p in root.iterdir() if p.is_dir() and p.name not in SKIP_DIRS):
        if re.match(r"^stage[-_]", child.name) and not STAGE_FOLDER.match(child.name):
            problems.append(f"{child.name}/ will not be found; name it stage-1 through stage-4")
        if not STAGE_FOLDER.match(child.name):
            continue
        if (child / ".git").exists():
            problems.append(f"{child.name}/ is a git repository of its own, so a clone gets an empty "
                            f"folder; delete {child.name}/.git and commit the files")
        for required in ("Dockerfile", "RUN.md"):
            if not (child / required).is_file():
                problems.append(f"{child.name}/{required} is missing")
    return problems


def unfilled(root: pathlib.Path) -> list[str]:
    problems = []
    for name in ("README.md", "FACTORY.md"):
        if not (root / name).is_file():
            continue
        lines = (root / name).read_text(errors="replace").splitlines()
        hits = [(number, line.count(UNFILLED)) for number, line in enumerate(lines, 1) if UNFILLED in line]
        if hits:
            problems.append(f"{name} still holds {sum(count for _n, count in hits)} run field(s) to fill "
                            f"(first on line {hits[0][0]})")
    return problems


def seat_files(root: pathlib.Path) -> tuple[list[str], list[str]]:
    """(names of the instruction files, problems)."""
    folder = root / "mandates"
    if not folder.is_dir():
        return [], ["gate 1: mandates/ is missing; every seat needs an instruction file"]
    files = sorted(folder.glob("*.md"))
    problems = []
    if len(files) < 3:
        problems.append(f"gate 1: {len(files)} instruction file(s) in mandates/; three or more are required")
    for path in files:
        text = path.read_text(errors="replace")
        for field in SEAT_FIELDS:
            if not re.search(rf"(?im)^[-*_ \t]*{field}[*_ \t]*:[*_ \t]*[^*_\s]", text):
                problems.append(f"gate 1: mandates/{path.name} has no `{field}:` line")
    return [p.stem for p in files], problems


def credentials(root: pathlib.Path) -> list[str]:
    problems = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (not path.is_file() or SKIP_DIRS & set(relative.parts)
                or (path.suffix.lower() not in TEXT_SUFFIXES and path.name not in TEXT_NAMES)):
            continue
        config = ((path.suffix.lower() in CONFIG_SUFFIXES or path.name in TEXT_NAMES)
                  and str(relative) != ROOM)
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for name, pattern in SECRETS:
            if name in CONFIG_ONLY and not config:
                continue
            if re.search(pattern, text):
                problems.append(f"{relative} holds something shaped like a credential ({name}); remove it "
                                f"and rotate the credential before the repository is public")
                break
    return problems


def when(message: dict):
    """The message's time, or None when no field this tool knows holds one."""
    for key in TIME_KEYS:
        value = message.get(key)
        if isinstance(value, bool) or value in (None, ""):
            continue
        try:
            if isinstance(value, (int, float)):
                seconds = value / 1000 if value > 1e11 else value
                return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc)
            stamp = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return stamp if stamp.tzinfo else stamp.replace(tzinfo=datetime.timezone.utc)
        except (ValueError, OverflowError, OSError):
            continue
    return None


def kind_of(message: dict) -> str:
    return str(message.get("senderType", "")).lower()


def is_text(message: dict) -> bool:
    return message.get("messageType") == "text"


def guarded(text: str, limit: int = 80) -> str:
    """Text of the room as it may be printed: nothing shaped or named like a credential, and no longer than
    the limit."""
    if any(re.search(pattern, text) for _name, pattern in SECRETS):
        return "[withheld: shaped like a credential]"
    return NAMED.sub(lambda m: m.group(1) + "[withheld]", text)[:limit]


def snippet(content) -> str:
    return guarded(" ".join(str(content or "").split()))


def clock(stamp) -> str:
    return stamp.strftime("%Y-%m-%d %H:%M") if stamp else "time unread"


def readable(messages: list) -> tuple[list, int]:
    """(messages this tool can read, how many it cannot). A sender, a content and a message type are text or
    absent."""
    good, bad = [], 0
    for m in messages:
        if any(m.get(key) is not None and not isinstance(m.get(key), str)
               for key in ("senderId", "senderName", "content", "messageType")):
            bad += 1
            continue
        good.append(m)
    return good, bad


def flat(line: str) -> str:
    """A line as it is compared: no blanks around it, one blank between its words."""
    return " ".join(line.split())


def held(message: dict) -> tuple[str, list[str]]:
    """(the id of the tool call, every text inside) of a tool call or a tool result. A download keeps both as
    JSON inside `content`: a call as {tool_call_id, name, args}, a result as {tool_call_id, name, output,
    is_error}. A text that is JSON itself is opened too, since a harness may keep its output that way."""
    call, texts, queue, opened = "", [], [str(message.get("content") or "")], 0
    while queue:
        item = queue.pop()
        if isinstance(item, dict):
            if not call and isinstance(item.get("tool_call_id"), str):
                call = item["tool_call_id"]
            queue.extend(item.values())
        elif isinstance(item, list):
            queue.extend(item)
        elif isinstance(item, str):
            inner = None
            if item[:1] in ("{", "[") and opened < MAX_OPENED:
                opened += 1
                try:
                    inner = json.loads(item)
                except (ValueError, RecursionError):
                    pass
            if isinstance(inner, (dict, list)):
                queue.append(inner)
            else:
                texts.append(item)
    return call, texts


def says(kind: str, body: str) -> tuple[dict, list[str]]:
    """({name: the line, as it is compared} for the lines of a card that decide, the names of those that every
    card of the kind holds and this one does not)."""
    found = {}
    for name in DECIDES[kind]:
        line = LINE[name].search(body)
        if line:
            found[name] = flat(line.group(0))
    return found, [name for name in DECIDES[kind] if name not in found and (kind, name) not in MAY_LACK]


def printed(lines: tuple, texts: list[str]) -> bool:
    """Whether a text holds the card: its first line and, below it and before any other card, the lines that
    say what it decided, in their order. A card that says nothing of the kind was printed nowhere."""
    if len(lines) < 2:
        return False
    for text in texts:
        rows = [flat(row) for row in text.splitlines()]
        for n, row in enumerate(rows):
            if row != lines[0]:
                continue
            wanted = list(lines[1:])
            for below in rows[n + 1:]:
                if below.startswith("CARD "):
                    break
                if below == wanted[0]:
                    del wanted[0]
                    if not wanted:
                        return True
    return False


def traces(lines: tuple, results: list, calls: dict) -> bool:
    """Whether one of the seat's tool results so far printed the card. A tool that was handed the card did
    not make it: the call that posts a message, or a command that repeats what it was given."""
    for call, texts in results:
        if any(lines[0] in flat(text) for text in calls.get(call, [])):
            continue
        if printed(lines, texts):
            return True
    return False


def read_room(root: pathlib.Path, stage_folders: int) -> dict:
    """Everything the room shows, as {problems, warnings, lines, rows, stage_minutes, notes}, the four counts
    of cards, the cards that do not trace and the lines that start like a card and are none."""
    out = {"problems": [], "warnings": [], "lines": [], "rows": [], "stage_minutes": {}, "notes": [],
           "cards_posted": 0, "cards_traced": 0, "cards_pasted_on": 0, "cards_never_posted_by_their_seat": 0,
           "untraced": [], "alike": []}
    path = root / ROOM
    if not path.is_file():
        out["problems"].append(f"{ROOM} is missing; {DOWNLOAD}")
        return out
    try:
        room = json.loads(path.read_text(errors="replace"))
        messages = room.get("messages") if isinstance(room, dict) else None
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            raise ValueError("expected an object whose `messages` is a list of message objects")
    except (ValueError, OSError, RecursionError) as exc:
        out["problems"].append(f"{ROOM} could not be read ({type(exc).__name__}: {exc}); {DOWNLOAD}")
        return out
    messages, unread = readable(messages)
    if unread:
        out["notes"].append(f"{unread} message(s) could not be read (a sender, a content or a message type that "
                            f"is not text) and are in no count below")
    if room.get("scope", "full") != "full":
        out["problems"].append(f"{ROOM} is a filtered download (scope `{room.get('scope')}`); "
                               f"use Download full session")

    seats = {m["senderId"]: m.get("senderName") or m["senderId"] for m in messages
             if m.get("senderId") and kind_of(m) == "agent"}
    names = sorted(set(seats.values()))
    out["lines"].append(f"{len(seats)} seats: {', '.join(names)}" if seats else "0 seats")
    types = {}
    for m in messages:                      # also the types that stand in no column, such as an error
        kind = m.get("messageType") or "without a type"
        types[kind] = types.get(kind, 0) + 1
    if types:
        out["lines"].append("messages by type: " + ", ".join(f"{guarded(kind)} {count}"
                                                             for kind, count in sorted(types.items())))
    if len(seats) < 3:
        out["problems"].append(f"gate 1: the room holds {len(seats)} agent seat(s); three or more are required")

    said = [m for m in messages if kind_of(m) == "agent" and is_text(m)]
    edges = {(m["senderId"], target) for m in said for target in seats
             if target != m.get("senderId") and f"@[[{target}]]" in str(m.get("content") or "")}
    pair = next(((a, b) for a, b in sorted(edges) if (b, a) in edges), None)
    if pair:
        out["lines"].append(f"reply in each direction: yes ({seats[pair[0]]} and {seats[pair[1]]})")
    else:
        out["lines"].append("reply in each direction: no")
        out["problems"].append("gate 2: two seats must address each other by @handle, with a reply in each "
                               "direction")

    if (root / "mandates").is_dir():
        have = {slug(p.stem) for p in (root / "mandates").glob("*.md")}
        missing = [name for name in names if slug(name) not in have]
        if missing:
            out["problems"].append(f"gate 1: no instruction file for {', '.join(missing)}; name each file "
                                   f"after its seat")

    tally = {}
    for m in said:
        tally[seats[m["senderId"]]] = tally.get(seats[m["senderId"]], 0) + 1
    total = sum(tally.values())
    if total:
        spread = ", ".join(f"{name} {count} of {total} ({round(100 * count / total)}%)"
                           for name, count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))
        out["lines"].append(f"work spread over seat messages: {spread}")
        top, count = max(tally.items(), key=lambda kv: kv[1])
        if total >= MIN_MESSAGES_FOR_SPREAD and count / total > LOPSIDED:
            out["warnings"].append(f"{top} sent {count} of {total} seat messages "
                                   f"({round(100 * count / total)}%); the rubric asks that more than one seat "
                                   f"did the work")

    humans = [m for m in messages if is_text(m) and m.get("senderId") and kind_of(m) not in NOT_HUMAN]
    out["lines"].append(f"{len(humans)} human message(s) for {stage_folders} stage folder(s)")
    for m in humans:
        out["lines"].append(f"  {clock(when(m))} {m.get('senderName') or m['senderId']}: "
                            f"{snippet(m.get('content'))}")
    if len(humans) > stage_folders:
        out["warnings"].append(f"{len(humans)} human message(s) for {stage_folders} stage folder(s); in the "
                               f"submitted run the dispatch is the only human input for its stage")

    costs(messages, seats, out)
    out["lines"].append(f"{out['cards_traced']} of {out['cards_posted']} cards trace to a tool run of the seat "
                        f"that posted them")
    out["lines"] += [f"  {line}" for line in out["untraced"]]
    if out.get("pasted"):
        out["lines"].append(out["pasted"])
    if out["alike"]:
        out["lines"].append(f"{len(out['alike'])} line(s) start like a card and are not in the form the tools "
                            f"print")
        out["lines"] += [f"  {line}" for line in out["alike"]]
    return out


def costs(messages: list, seats: dict, out: dict) -> None:
    """One row per stage and seat. A gap between two messages is given to the seat that posted next.
    A card counts once, for the seat that wrote it: a seat that pastes it on adds nothing."""
    rows, first, last = {}, {}, {}
    stage, previous, timed, backwards = 0, None, False, 0
    results, calls, cut, untraced, cut_before = {}, {}, {}, {}, {}
    own, pasted, unowned, alike = set(), 0, {}, {}
    for m in messages:
        if m.get("messageType") in ROOM_EVENTS:
            continue
        content = str(m.get("content") or "")
        cards = list(CARD.finditer(content)) if kind_of(m) == "agent" and is_text(m) else []
        if cards:
            stage = int(cards[0].group(2))
        elif kind_of(m) not in NOT_HUMAN and is_text(m):
            named = re.search(r"\bstage[ -](\d)\b", content, re.I)
            if named:
                stage = int(named.group(1))
        stamp = when(m)
        if stamp:
            timed = True
            first.setdefault(stage, stamp)
            last[stage] = stamp
        if kind_of(m) == "agent" and m.get("senderId") in seats:
            sender, seat = m["senderId"], seats[m["senderId"]]
            row = rows.setdefault((stage, seat), {
                "stage": stage, "seat": seat, "seconds": 0, "messages": 0, "handoffs": 0,
                "rejections": 0, "findings_opened": 0, "findings_closed": 0, "findings_withdrawn": 0,
                "cards_posted": 0, "cards_traced": 0})
            if stamp and previous:
                if stamp < previous:
                    backwards += 1
                row["seconds"] += max(0, (stamp - previous).total_seconds())
            if is_text(m):
                row["messages"] += 1
            if m.get("messageType") == TOOL_RESULT:
                call, texts = held(m)
                if any(text.rstrip().endswith(CUT) for text in texts):
                    cut[sender] = cut.get(sender, 0) + 1
                if "CARD " in content:
                    results.setdefault(sender, []).append((call, texts))
            elif m.get("messageType") == TOOL_CALL and "CARD " in content:
                call, texts = held(m)
                if call:
                    calls.setdefault(sender, {})[call] = texts
            for line in content.splitlines() if is_text(m) else []:
                if CARD_LIKE.match(line) and not CARD.search(line):     # meant as a card, and none
                    alike[seat] = alike.get(seat, 0) + 1
                    out["alike"].append(f"not a card: {clock(stamp)} {seat}: "
                                        f"{guarded(MENTIONS.sub('', line).rstrip(), 120)}")
            for n, card in enumerate(cards):
                body = content[card.end():cards[n + 1].start() if n + 1 < len(cards) else len(content)]
                end = content.find("\n", card.start())
                found, lacking = says(card.group(1), body)
                lines = (flat(content[card.start():end if end >= 0 else len(content)]), *found.values())
                kind = (card.group(1), int(card.group(2)))
                if slug(card.group(3)) != slug(seat):       # pasted on by another seat
                    pasted += 1
                    if lines not in own:                    # and the seat it names never posted it
                        kinds = unowned.setdefault((seat, card.group(3)), {})
                        kinds[kind] = kinds.get(kind, 0) + 1
                    continue
                own.add(lines)
                row["cards_posted"] += 1
                if not lacking and traces(lines, results.get(sender, []), calls.get(sender, {})):
                    row["cards_traced"] += 1
                else:
                    kinds = untraced.setdefault(seat, {})
                    kinds[kind] = kinds.get(kind, 0) + 1
                    cut_before[seat] = cut.get(sender, 0)
                    decided = [guarded(line, 120) for line in found.values()]
                    decided += [f"no {name} line" for name in lacking]
                    out["untraced"].append(f"not traced: {clock(stamp)} {seat}, {kind[0]} card of stage {kind[1]} "
                                           f"({' / '.join(decided)})")
                if card.group(1) == "handoff":
                    row["handoffs"] += 1
                elif card.group(1) == "verdict" and re.match(r"verdict: REJECT\b", found.get("verdict", "")):
                    row["rejections"] += 1
                elif card.group(1) == "finding" and STATUS.search(found.get("check", "")):
                    status = STATUS.search(found["check"]).group(1)
                    row["findings_opened" if status == "open" else f"findings_{status}"] += 1
        previous = stamp or previous
    for key in sorted(rows):
        row = rows[key]
        row["minutes"] = round(row.pop("seconds") / 60) if timed else None
        out["rows"].append(row)
    out["stage_minutes"] = {str(s): round((last[s] - first[s]).total_seconds() / 60) for s in sorted(first)}
    traced(out, untraced, cut_before, pasted, unowned)
    if alike:
        out["warnings"].append(
            f"{sum(alike.values())} line(s) in seat messages start like a card and are not in the form the tools "
            f"print, CARD <type> | stage <n> | <from> -> <to>: "
            f"{', '.join(f'{seat} {count}' for seat, count in sorted(alike.items()))}. Each is listed in the "
            f"room section. They stand in no count of the cost table and were not traced; post what the tool "
            f"prints, unchanged")
    if any(row["stage"] == 0 for row in out["rows"]):
        out["notes"].append("stage 0 holds what was posted before a card or a human message named a stage")
    if backwards:
        out["notes"].append(f"{backwards} message(s) carry a time earlier than the message before them; "
                            f"their gaps count as 0 minutes")
    if not timed and messages:
        out["notes"].append("could not read a time on any message (looked for: " + ", ".join(TIME_KEYS)
                            + "); the minutes are unread and must be measured another way")
    out["notes"].append("minutes are wall-clock gaps given to the seat that posted next, not billed compute")
    out["notes"].append("model spend is not in the room; copy it from each provider's usage page into "
                        "FACTORY.md")


def counted(kinds: dict) -> str:
    return ", ".join(f"{count} {kind} card{'s' if count > 1 else ''} of stage {number}"
                     for (kind, number), count in kinds.items())


def traced(out: dict, untraced: dict, cut_before: dict, pasted: int, unowned: dict) -> None:
    """The totals, and a warning for each kind of card that no seat can answer for: one that no tool run of
    its seat printed, and one posted in the name of a seat that never posted it."""
    out["cards_posted"] = sum(row["cards_posted"] for row in out["rows"])
    out["cards_traced"] = sum(row["cards_traced"] for row in out["rows"])
    out["cards_pasted_on"] = pasted
    out["cards_never_posted_by_their_seat"] = sum(sum(kinds.values()) for kinds in unowned.values())
    if untraced:
        named = []
        for seat in sorted(untraced):
            posted = sum(row["cards_posted"] for row in out["rows"] if row["seat"] == seat)
            named.append(f"{seat}, {sum(untraced[seat].values())} of its {posted} ({counted(untraced[seat])})")
            if cut_before[seat]:
                out["notes"].append(f"{cut_before[seat]} tool result(s) of {seat} end with an ellipsis, the mark "
                                    f"of a result the download cut short; a card printed after the cut cannot "
                                    f"be traced")
        out["warnings"].append(
            f"{out['cards_posted'] - out['cards_traced']} of {out['cards_posted']} card(s) do not trace to a "
            f"tool run of the seat that posted them: {'; '.join(named)}. Each is listed in the room section "
            f"with the time it was posted. No tool result of that seat, earlier in the room, holds the card's "
            f"first line and below it the same verdict, check and revision lines. That is how a card reads "
            f"that was typed by hand or changed after a tool printed it; compare it with the card file")
    if pasted:
        out["pasted"] = (f"{pasted} card(s) pasted on by another seat, "
                         f"{pasted - out['cards_never_posted_by_their_seat']} of them posted before by the seat "
                         f"they name")
    if unowned:
        named = "; ".join(f"{seat} posted {counted(kinds)} in the name of {snippet(name)}"
                          for (seat, name), kinds in sorted(unowned.items()))
        out["warnings"].append(
            f"{out['cards_never_posted_by_their_seat']} card(s) stand in the room in the name of a seat that "
            f"never posted them: {named}. A card that is pasted on stands earlier in the room, posted by the "
            f"seat it names, with the same first line and the same verdict, check and revision lines. These "
            f"are in no count of the cost table")


def table(rows: list) -> list[str]:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        minutes = "unread" if r["minutes"] is None else r["minutes"]
        lines.append(f"| {r['stage']} | {r['seat']} | {minutes} | {r['messages']} | {r['handoffs']} | "
                     f"{r['rejections']} | {r['findings_opened']} | {r['findings_closed']} | "
                     f"{r['findings_withdrawn']} | {r['cards_traced']} of {r['cards_posted']} |")
    return lines


def official(harness: pathlib.Path | None, root: pathlib.Path, track: str) -> tuple[str, list[str]]:
    """(the line to print, problems). The organizers' checker is the authority when it is present."""
    if harness is None:
        return ("official checker: not run (pass --harness <kickoff checkout>); the vocabulary scan of the "
                "seat files was not done here"), []
    if not (harness / "harness" / "check.py").is_file():
        return (f"official checker: not run, no harness/check.py under {harness}",
                [f"the official checker was asked for and is not under {harness}"])
    try:
        done = subprocess.run([sys.executable, "-m", "harness", "check", str(root.resolve()), "--track", track],
                              cwd=harness, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"official checker: could not run ({exc})", [f"the official checker could not run: {exc}"]
    lines = [line for line in (done.stdout + done.stderr).splitlines() if line.strip()]
    if done.returncode == 0:
        return "official checker: 0 problem(s)", []
    found = [line for line in lines if not re.fullmatch(r"\d+ problem\(s\)", line.strip())]
    return f"official checker: {len(found)} problem(s)", found or [f"the official checker exited {done.returncode}"]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="preflight.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("repo")
    parser.add_argument("--track", required=True)
    parser.add_argument("--harness")
    parser.add_argument("--costs-out")
    args = parser.parse_args(argv[1:])
    root = pathlib.Path(args.repo)
    if not root.is_dir():
        print(f"{root} is not a directory")
        return 2
    for stream in (sys.stdout, sys.stderr):     # a name in the room may hold what this terminal cannot show
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    try:
        return examine(root, args)
    except Exception as exc:                # an error in this tool says nothing about the repository
        stale = ""
        if args.costs_out and pathlib.Path(args.costs_out).is_file():
            try:
                pathlib.Path(args.costs_out).unlink()
                stale = f" {args.costs_out} came from an earlier run and was removed."
            except OSError:
                stale = f" {args.costs_out} comes from an earlier run: do not use it."
        print(f"preflight could not finish: {type(exc).__name__}: {exc}. This is an error of the tool. Nothing "
              f"was judged: the repository is neither ready nor refused.{stale}")
        return 3


def examine(root: pathlib.Path, args) -> int:
    print(f"preflight: {root} (track {args.track})")
    problems = layout(root) + unfilled(root)
    names, seat_problems = seat_files(root)
    problems += seat_problems
    print(f"seat files: {len(names)} ({', '.join(names) or 'none'})")
    stage_folders = len([p for p in root.iterdir() if p.is_dir() and STAGE_FOLDER.match(p.name)])
    room = read_room(root, stage_folders)
    problems += room["problems"]
    print("room:")
    for line in room["lines"] or ["  not read"]:
        print(f"  {line}")
    if room["rows"]:
        print("costs:")
        for line in table(room["rows"]):
            print(f"  {line}")
        for stage, minutes in room["stage_minutes"].items():
            print(f"  stage {stage}: {minutes} minute(s) from its first message to its last")
    for note in room["notes"]:
        print(f"  note: {note}")
    problems += credentials(root)
    line, found = official(pathlib.Path(args.harness) if args.harness else None, root, args.track)
    print(line)
    for problem in found:
        print(f"  official: {problem}")
    for problem in problems:
        print(f"problem: {problem}")
    for warning in room["warnings"]:
        print(f"warning: {warning}")
    if args.costs_out:
        try:
            pathlib.Path(args.costs_out).write_text(json.dumps(
                {"rows": room["rows"], "stage_minutes": room["stage_minutes"],
                 "cards_posted": room["cards_posted"], "cards_traced": room["cards_traced"],
                 "cards_pasted_on": room["cards_pasted_on"],
                 "cards_never_posted_by_their_seat": room["cards_never_posted_by_their_seat"],
                 "card_like_lines": len(room["alike"]), "notes": room["notes"]}, indent=1))
        except OSError as exc:
            print(f"problem: could not write {args.costs_out}: {exc}")
            problems.append("costs file not written")
    count = len(problems) + len(found)
    tail = f", {len(room['warnings'])} warning(s)" if room["warnings"] else ""
    print(f"result: {count} problem(s){tail}" if count else f"result: ready{tail}")
    return 1 if count else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
