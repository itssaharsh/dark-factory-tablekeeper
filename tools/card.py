#!/usr/bin/env python3
"""Fixed-field cards for every handoff, verdict and finding.

A card is what one seat hands another. It carries the whole task or the whole
finding, never a pointer to an earlier message, so a seat that sees only what is
addressed to it can act on the card alone.

usage: card.py validate <file.json>      exit 0 when valid, 1 with the problems listed
       card.py render <file.json>        the fixed-format text to post in the room
Standard library only.
"""
from __future__ import annotations

import json
import re
import sys

REQUIRED = {
    "handoff": ["card", "stage", "from", "to", "task", "revision_in", "revision_out", "commands",
                "claim", "changed_tests", "at"],
    "verdict": ["card", "stage", "from", "to", "revision", "base", "runs", "findings",
                "verdict", "at"],
    "finding": ["card", "stage", "from", "to", "check_id", "sentence", "kind", "replay",
                "expected", "observed", "status", "at"],
}
VERDICTS = ("ACCEPT", "REJECT")
STATUSES = ("open", "closed", "withdrawn")
KINDS = ("literal", "behaviour", "concurrency")
# A pointer is not a handoff: the receiving seat may not be able to read the room.
POINTER = re.compile(r"\b(see|read|check|refer to)\s+(the\s+|my\s+|our\s+)?"
                     r"(room|thread|chat|above|(earlier|previous|last)\s+(message|handoff|task))\b"
                     r"|\b(message|task)[ _-]?id\b", re.I)
MIN_TASK_CHARS = 40


def _commands(items, name: str, need: tuple[str, ...]) -> list[str]:
    if not isinstance(items, list):
        return [f"{name} must be a list"]
    problems = []
    for n, item in enumerate(items):
        if not isinstance(item, dict):
            problems.append(f"{name}[{n}] must be an object")
            continue
        for key in need:
            if key not in item:
                problems.append(f"{name}[{n}] lacks {key}")
        if "exit_code" in item and (isinstance(item["exit_code"], bool) or not isinstance(item["exit_code"], int)):
            problems.append(f"{name}[{n}].exit_code must be an integer")
    return problems


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _line(value) -> str:
    """Free text on one line: a line break inside a field must not pass for a line of the card."""
    return " ".join(str(value).split())


def validate(card: dict) -> list[str]:
    """Every problem with the card, in field order. Empty means valid."""
    if not isinstance(card, dict):
        return ["a card is a JSON object"]
    kind = card.get("card")
    if kind not in REQUIRED:
        return [f"card must be one of {sorted(REQUIRED)}, not {kind!r}"]
    problems = [f"{kind} card lacks {field}" for field in REQUIRED[kind]
                if field not in card or card[field] in ("", None)]
    if isinstance(card.get("stage"), bool) or not isinstance(card.get("stage"), int):
        problems.append("stage must be an integer")
    if kind == "handoff":
        task = str(card.get("task") or "").strip()
        if len(task) < MIN_TASK_CHARS or POINTER.search(task):
            problems.append("task must carry the whole task text, not a pointer to the room or a message")
        if "commands" in card:
            problems += _commands(card["commands"], "commands", ("cmd", "exit_code"))
            if card["commands"] == []:
                problems.append("commands must list at least one command that was run, with its exit code")
        tests = card.get("changed_tests")
        if "changed_tests" in card and not (isinstance(tests, list) and all(isinstance(p, str) for p in tests)):
            problems.append("changed_tests must be a list of paths (empty when no test file changed)")
    elif kind == "verdict":
        if "runs" in card:
            problems += _commands(card["runs"], "runs", ("tree", "cmd", "exit_code"))
            if card["runs"] == []:
                problems.append("runs must hold at least the run on the posted revision")
            elif isinstance(card["runs"], list) and card.get("verdict") == "ACCEPT" and any(
                    isinstance(r, dict) and r.get("tree") == "revision" and r.get("exit_code") != 0 for r in card["runs"]):
                problems.append("an ACCEPT cannot hold a failing run on the posted revision")
        if card.get("verdict") not in VERDICTS:
            problems.append(f"verdict must be one of {VERDICTS}")
        if not isinstance(card.get("findings"), list):
            problems.append("findings must be a list")
        elif card.get("verdict") == "REJECT" and not card["findings"]:
            problems.append("a REJECT needs at least one finding that says what failed")
        elif card.get("verdict") == "ACCEPT" and card["findings"]:
            problems.append("an ACCEPT cannot hold a finding: a revision with a finding is a REJECT")
    elif kind == "finding":
        if card.get("status") not in STATUSES:
            problems.append(f"status must be one of {STATUSES}")
        # A check that was wrong is taken back in the open, never deleted: the reason stays on the card.
        elif card["status"] == "withdrawn" and not _text(card.get("withdrawn_reason")).strip():
            problems.append("a withdrawn card needs withdrawn_reason: the reason the check was wrong, in words")
        if "revision" in card and not re.fullmatch(r"\S+", _text(card["revision"])):
            problems.append("revision must be the revision that was checked, as text without white space")
        # What a card keeps of the times its check was taken back before it failed again.
        before = card.get("withdrawn_before")
        if "withdrawn_before" in card and not (isinstance(before, list) and before and all(
                isinstance(w, dict) and _text(w.get("reason")).strip() and isinstance(w.get("at", ""), str)
                for w in before)):
            problems.append("withdrawn_before must list the earlier withdrawals, each with its reason in words")
        if card.get("kind") not in KINDS:
            problems.append(f"kind must be one of {KINDS}")
        if "replay" in card and not isinstance(card["replay"], dict):
            problems.append("replay must be an object holding the exact requests")
        elif isinstance(card.get("replay"), dict) and not (card["replay"].get("steps") or card["replay"].get("burst")):
            problems.append("replay must hold the requests to send: steps or a burst")
    return problems


def render(card: dict) -> str:
    """The text a seat posts in the room. Same fields, same order, every time."""
    kind = card.get("card")
    lines = [f"CARD {kind} | stage {card.get('stage')} | {card.get('from')} -> {card.get('to')}"]
    if kind == "handoff":
        lines.append(f"revision: {card.get('revision_out')} (started from {card.get('revision_in')})")
        lines.append(f"claim: {card.get('claim')}")
        for c in card.get("commands", []):
            lines.append(f"ran: {c.get('cmd')} (exit {c.get('exit_code')})")
        lines.append("changed tests: " + (", ".join(card.get("changed_tests") or []) or "none"))
        for note in card.get("spec_change_notes", []):
            lines.append(f"spec change: {note}")
        lines += ["task:", str(card.get("task"))]
    elif kind == "verdict":
        lines.append(f"verdict: {card.get('verdict')}")
        lines.append(f"revision: {card.get('revision')} (base {card.get('base')})")
        for r in card.get("runs", []):
            lines.append(f"ran on {r.get('tree')}: {r.get('cmd')} (exit {r.get('exit_code')})")
        for f in card.get("findings", []):
            lines.append(f"finding: {f}")
        for note in card.get("spec_change_notes", []):
            lines.append(f"spec change: {note}")
        for note in card.get("notes", []):
            lines.append(f"note: {note}")
    elif kind == "finding":
        lines.append(f"check: {card.get('check_id')} ({card.get('kind')}) status: {card.get('status')}")
        if card.get("status") == "withdrawn":
            lines.append(f"withdrawn: {_line(card.get('withdrawn_reason'))}")
        before = card.get("withdrawn_before")
        for w in before if isinstance(before, list) else []:
            w = w if isinstance(w, dict) else {}
            lines.append(f"withdrawn before: {_line(w.get('reason'))}" + (f" ({_line(w['at'])})" if w.get("at") else ""))
        if "revision" in card:
            lines.append(f"revision: {_line(card.get('revision'))}")
        lines.append(f"requirement: {card.get('sentence')}")
        lines.append(f"expected: {card.get('expected')}")
        lines.append(f"observed: {card.get('observed')}")
        lines.append("replay: " + json.dumps(card.get("replay"), sort_keys=True))
    lines.append(f"at: {card.get('at')}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("validate", "render"):
        print(__doc__)
        return 2
    try:
        card = json.loads(open(argv[2], encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        print(f"could not read {argv[2]}: {exc}")
        return 3
    problems = validate(card)
    if argv[1] == "validate":
        for p in problems:
            print(p)
        print("valid" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0
    if problems:
        print("refusing to render an invalid card:")
        for p in problems:
            print(" ", p)
        return 1
    print(render(card))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
