#!/usr/bin/env python3
"""Run a per-sentence check list against a served service, black-box.

One entry per requirement sentence. Each entry is either a sequence of requests with
expectations, or a burst of requests released together with a read-back afterwards.
A failure becomes a finding card holding the exact requests, so whoever fixes it can
replay it, and the card closes only when the replay passes.

usage: speccheck.py run --checks FILE --base-url URL --stage N [--cards DIR] [--repeat 3]
                        [--spec FILE ...] [--accepted CARD.json]
       speccheck.py replay CARD.json --base-url URL [--repeat 3]
       speccheck.py coverage --checks FILE [--spec FILE ...]
       speccheck.py withdraw CARD.json --reason "why the check was wrong"
exit:  0 everything passed · 1 at least one entry failed · 2 the check list, the card, a
       requirements file or the address is unusable · 3 the service could not be reached (an
       error is not a finding; cards already written for earlier entries are kept and named)
A card is written the moment its entry fails. Values saved from responses and anything shaped
like a credential are masked in a card, so a card can be committed.
With --spec every quoted sentence is looked up in the requirements files, and the lines of those
files that hold a requirement word and that no entry quotes are counted. One file or several may
follow --spec, and it may be given more than once. With --accepted the run takes the verdict
card of the revision and refuses one that is not an ACCEPT.
The entry format is described in CHECKS.md. Standard library only.
"""
from __future__ import annotations

import argparse
import bisect
import datetime
import hashlib
import http.client
import json
import pathlib
import re
import sys
import threading
import urllib.parse

import card as cards

MISSING = object()
KINDS = ("literal", "behaviour", "concurrency")
TIMEOUT = 5.0            # the per-request limit the service is judged under
WHOLE = re.compile(r"^\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}$")
PART = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list,
         "object": dict, "null": type(None)}
MATCHERS = {"type", "exists", "regex", "ne", "len", "in", "eq"}
ENTRY_KEYS = {"id", "sentence", "kind", "setup", "steps", "burst", "readback", "no_readback", "unchecked"}
STEP_KEYS = {"request", "expect", "save"}
REQUEST_KEYS = {"method", "path", "headers", "json", "body"}
EXPECT_KEYS = {"status", "json", "header", "testids", "body_empty", "body_contains"}
BURST_KEYS = {"n", "request", "requests", "expect"}
BURST_EXPECT_KEYS = {"status_counts", "max_status_counts", "min_status_counts", "distinct", "values_are_range",
                     "identical_bodies", "no_5xx"}
MOST_IN_FLIGHT = 50      # the load the service is judged under
MIN_REASON = 20          # characters of the reason a card is withdrawn for; a shorter one says nothing
MIN_WORDS = 2            # words of the reason an entry reads nothing back for
# A line of a requirements file that holds one of these reads like a requirement.
REQUIREMENT_WORDS = ("must", "shall", "never", "exactly", "at most", "at least", "only")
REQUIREMENT = re.compile(r"(?i)\b(?:" + "|".join(word.replace(" ", r"\s+") for word in REQUIREMENT_WORDS) + r")\b")
# What Markdown adds to a sentence, and a seat leaves out when it quotes: the marker that opens a list
# item, a heading or a quoted block, emphasis marks and back-ticks. An underscore inside a name stays.
MARKER = re.compile(r"^\s*(?:>\s*)*(?:(?P<number>\d+)[.)]\s+|(?P<bullet>[-*+])\s+|#{1,6}\s+)?")
# A line that no paragraph runs through: it is empty, a heading, a row of a table, a fence or a rule.
NO_PROSE = re.compile(r"^\s*(?:>\s*)*(?:$|#{1,6}\s|\||```|~~~|(?:[-*_]\s*){3,}$)")
EMPHASIS = re.compile(r"[*`]+|(?<![A-Za-z0-9])_+|_+(?![A-Za-z0-9])")
SENTENCE_ENDS = ".!?:;|"  # after one of these a sentence or a table cell begins, before one it ends
# A sentence also begins after a bracket or a quotation mark that opens, and ends before one that closes.
OPENED = re.compile(r"(?<!\w)[(\[{\"'\u201c\u2018\u00ab]+$")
CLOSED = re.compile(r"^[)\]}\"'\u201d\u2019\u00bb]+(?!\w)")
# And after a short label at the start of its line: one that holds a digit or stands in brackets,
# such as R2, S1-7.4, [R3] or (a). A word is no label, so the end of a sentence is no sentence.
LABEL = re.compile(r"(?:\([\w.\-]{1,12}\)|\[[\w.\-]{1,12}\]|(?=[\w.\-]*\d)\w[\w.\-]{0,11})[:.)]?"
                   r"(?:\s+[-\u2013\u2014])?")
SHOWN = 40               # characters printed of what stands before or after a part of a sentence
MOST_LINES = 5           # lines of a requirements file that one quoted requirement may run over
NAMED = 10               # line numbers printed for each requirements file
CUT = "\u2026"           # stands where a long sentence was cut short
# Never written into a card: the shapes a public repository is scanned for, values the entry used
# as a credential, and values the service returned under a name that says they are one.
MASKS = (("bearer-token", r"(?i)\bbearer\s+(?=[A-Za-z0-9._\-]*\d)[A-Za-z0-9._\-]{20,}"),
         ("api-key", r"\bsk-[A-Za-z0-9._\-]{16,}"), ("aws-access-key", r"\bAKIA[0-9A-Z]{16}\b"),
         ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
         ("url-credentials", r"""(?<=://)[^/\s:@"'\\]+:[^/\s@"'\\]+(?=@)"""))
TELLING = {"token", "tokens", "jwt", "secret", "secrets", "password", "passwords", "passwd", "pwd", "apikey",
           "authorization", "cookie", "cookies", "credential", "credentials", "pin", "otp"}
TELLING_PAIRS = {("api", "key"), ("session", "id"), ("session", "key")}
# A name that ends in one of these describes a credential and does not hold one: tokens_used, password_policy.
PLAIN_ENDINGS = {"policy", "length", "enabled", "disabled", "count", "type", "used", "expires", "expiry", "expired",
                 "min", "max", "rules", "hint", "strength", "age", "required", "valid", "invalid", "error", "errors",
                 "attempts", "limit", "ttl", "name", "url"}
# The shape the organizers' scanner reads as a credential in .json files, which is what a card is.
ASSIGNED = re.compile(r"(?i)\b[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\s*=\s*\S+")
# Both start where a name can start and bound the name, so a long run of letters or quotes costs no more.
PAIR = re.compile(r"""(?<![\w-])([A-Za-z][\w-]{0,60})=([^&\s"']+)""")
JSON_PAIR = re.compile(r'(?<!\\)"([^"\\]{1,80})"(\s*:\s*)("(?:[^"\\]|\\.)*"|-?\d[\d.eE+-]*)')
PATH = re.compile(r"^(?:\$|[^$.\[\]\s][^.\[\]]*)(?:\.[^.\[\]]+|\[\d+\])*$")
KEPT = 400               # characters of a body that a card keeps, counted after masking
MASKED = "[masked]"
# A header pattern that matches every one of these matches anything, and so checks nothing.
PROBES = ("", "zq9 \u2603 probe", "public, max-age=60", "text/html; charset=iso-8859-1", "0", "application/json",
          "a=b; Path=/", "<a>;,b", "x" * 300)


class Unreachable(Exception):
    pass


class Unusable(Exception):
    """The check list, the card or the address cannot be used. Never a finding against the service."""


def words_of(name) -> list:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", re.sub(r"\[\d+\]", "", str(name))).lower()
    return [part for part in re.split(r"[^a-z0-9]+", text) if part]


def telling(name, parent=None) -> bool:
    """Whether a name says that its value is a credential. For a path such as $.session.id every part
    of the way counts: what lies under `credentials` is one. A word such as token or password says so
    wherever it stands in a name, unless the name ends in a word that only describes: tokens_used,
    password_policy."""
    if parent is None and "." in str(name).lstrip("$."):
        steps = [step for step in str(name).lstrip("$").split(".") if step]
        return any(telling(step, steps[n - 1] if n else None) for n, step in enumerate(steps))
    parts = words_of(name)
    if not parts:
        return False
    if any(part in TELLING for part in parts) and parts[-1] not in PLAIN_ENDINGS:
        return True
    return tuple(((words_of(parent) if parent is not None else []) + parts)[-2:]) in TELLING_PAIRS


# ---------------------------------------------------------------- values and templates
def fill(value, variables: dict):
    """Replace {{name}}. A string that is only a placeholder keeps the variable's type."""
    if isinstance(value, str):
        whole = WHOLE.match(value)
        if whole and whole.group(1) in variables:
            return variables[whole.group(1)]
        return PART.sub(lambda m: str(variables.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: fill(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, variables) for v in value]
    return value


def pick(body, path: str):
    """`$.a.b[0].c` into a parsed JSON body. MISSING when the path is not there."""
    path = path.strip()
    if path in ("$", ""):
        return body
    current = body
    for key, index in re.findall(r"\.([^.\[\]]+)|\[(\d+)\]", path[1:] if path.startswith("$") else "." + path):
        try:
            current = current[key] if key else current[int(index)]
        except (KeyError, IndexError, TypeError):
            return MISSING
    return current


def is_type(value, name: str) -> bool:
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, TYPES[name])


def match_json(body, expected: dict) -> list[str]:
    problems = []
    for path, want in (expected or {}).items():
        got = pick(body, path)
        shown = "missing" if got is MISSING else json.dumps(got)
        if got is not MISSING and telling(path.strip()):
            shown = MASKED
        if isinstance(want, dict) and want and set(want) <= MATCHERS:
            if "eq" in want and (got is MISSING or got != want["eq"]):
                problems.append(f"{path.strip()}: expected {json.dumps(want['eq'])}, observed {shown}")
            if "exists" in want and (got is not MISSING) != bool(want["exists"]):
                problems.append(f"{path.strip()}: expected {'present' if want['exists'] else 'absent'}, observed {shown}")
            if got is MISSING:
                if any(k in want for k in ("type", "regex", "ne", "len", "in")) and "eq" not in want:
                    problems.append(f"{path.strip()}: missing")
                continue
            if "type" in want and not is_type(got, want["type"]):
                problems.append(f"{path.strip()}: expected type {want['type']}, observed {shown}")
            if "regex" in want and not (isinstance(got, str) and re.search(want["regex"], got)):
                problems.append(f"{path.strip()}: expected to match {want['regex']}, observed {shown}")
            if "ne" in want and got == want["ne"]:
                problems.append(f"{path.strip()}: expected anything but {json.dumps(want['ne'])}")
            if "len" in want and (not hasattr(got, "__len__") or len(got) != want["len"]):
                problems.append(f"{path.strip()}: expected length {want['len']}, observed {shown}")
            if "in" in want and got not in want["in"]:
                problems.append(f"{path.strip()}: expected one of {json.dumps(want['in'])}, observed {shown}")
        elif got is MISSING or got != want or isinstance(got, bool) != isinstance(want, bool):
            problems.append(f"{path.strip()}: expected {json.dumps(want)}, observed {shown}")
    return problems


# ---------------------------------------------------------------- one request
class Response:
    def __init__(self, status: int, headers: dict, raw: bytes, error: str = ""):
        self.status, self.headers, self.raw, self.error = status, headers, raw, error
        try:
            self.json = json.loads(raw) if raw else None
        except (ValueError, RecursionError):        # not JSON, or nested too deep to read
            self.json = MISSING

    def brief(self) -> dict:
        text = self.raw.decode("utf-8", "replace")
        # The whole body, so that it can be read and masked before a card keeps the start of it.
        return {"status": self.status, "body": text[:2000000], **({"error": self.error} if self.error else {})}


def connect(base_url: str, timeout: float = TIMEOUT):
    """An open connection. No connection at all is an error, never a finding."""
    url = urllib.parse.urlsplit(base_url)
    conn_type = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conn = conn_type(url.hostname, url.port, timeout=timeout)
    try:
        conn.connect()
    except OSError as exc:
        conn.close()
        raise Unreachable(f"{type(exc).__name__}: {exc}") from exc
    return conn


def exchange(conn, base_url: str, request: dict, timeout: float = TIMEOUT) -> Response:
    """Send one request and read the whole answer before the deadline: the limit is for the request,
    not for each read. Connected, then slow or broken, is a finding."""
    url = urllib.parse.urlsplit(base_url)
    method = str(request.get("method", "GET")).upper()
    headers = {str(k): str(v) for k, v in (request.get("headers") or {}).items()}
    body = None
    if "json" in request:
        body = json.dumps(request["json"]).encode()
        headers.setdefault("Content-Type", "application/json")
    elif "body" in request:
        body = str(request["body"]).encode()
    sock = conn.sock            # kept: the connection drops its own reference when the answer closes it
    late = threading.Event()

    def expire():               # the limit is for the request: sending, headers and body together
        late.set()
        try:
            sock.shutdown(2)
        except OSError:
            pass

    clock = threading.Timer(timeout, expire)
    clock.daemon = True
    clock.start()
    try:
        conn.request(method, (url.path.rstrip("/") + str(request.get("path", "/"))) or "/", body=body, headers=headers)
        got = conn.getresponse()
        raw = got.read()
        if late.is_set():
            raise TimeoutError
        return Response(got.status, {k.lower(): v for k, v in got.getheaders()}, raw)
    except (OSError, http.client.HTTPException, ValueError) as exc:
        if late.is_set() or isinstance(exc, TimeoutError):
            return Response(0, {}, b"", error=f"no complete response within {timeout:g} s")
        return Response(0, {}, b"", error=f"{type(exc).__name__}: {exc}")
    finally:
        clock.cancel()
        conn.close()


def send(base_url: str, request: dict, timeout: float = TIMEOUT) -> Response:
    return exchange(connect(base_url, timeout), base_url, request, timeout)


def judge(response: Response, expect: dict) -> list[str]:
    problems = []
    expect = expect or {}
    if response.error:
        return [f"no response: {response.error}"]
    if "status" in expect:
        allowed = expect["status"] if isinstance(expect["status"], list) else [expect["status"]]
        if response.status not in allowed:
            problems.append(f"status: expected {allowed if len(allowed) > 1 else allowed[0]}, observed {response.status}")
    if expect.get("body_empty") and response.raw:
        problems.append(f"body: expected empty, observed {len(response.raw)} bytes")
    if "json" in expect:
        if response.json is MISSING or response.json is None:
            problems.append("body: expected JSON, observed none or unparseable")
        else:
            problems += match_json(response.json, expect["json"])
    for name, want in (expect.get("header") or {}).items():
        got = response.headers.get(name.lower())
        if got is None or not re.search(str(want), got):
            seen = MASKED if got is not None and telling(name) else got
            problems.append(f"header {name}: expected to match {want}, observed {seen}")
    text = response.raw.decode("utf-8", "replace")
    for testid in expect.get("testids") or []:
        if not re.search(r"data-testid\s*=\s*[\"']" + re.escape(testid) + r"[\"']", text):
            problems.append(f"test id {testid}: not in the served page")
    for needle in expect.get("body_contains") or []:
        if needle not in text:
            problems.append(f"body: expected to contain {needle!r}")
    return problems


# ---------------------------------------------------------------- entries
class Remembering(dict):
    """The variables of one round. Every value ever saved is also kept in `seen`."""

    def __init__(self, seen: dict):
        super().__init__()
        self.seen = seen

    def __setitem__(self, name, value):
        super().__setitem__(name, value)
        self.seen.setdefault(name, []).append(value)


def secrets_of(entry: dict, seen: dict) -> dict:
    """{name: values} for every saved value that is a credential: saved from a place, or under a name,
    that says so, or sent in a header by any request of the entry. Other saved values stay readable."""
    used = set()
    blocks = [entry.get(k) or [] for k in ("setup", "steps", "readback")]
    burst = entry.get("burst") or {}
    requests = [s.get("request") or {} for block in blocks for s in block if isinstance(s, dict)]
    requests += list(burst.get("requests") or []) + [burst.get("request") or {}]
    for request in requests:
        for value in (request.get("headers") or {}).values():
            used.update(PART.findall(str(value)))
    for block in blocks:
        for step in block:
            for name, path in ((step.get("save") or {}) if isinstance(step, dict) else {}).items():
                if telling(name) or telling(path):
                    used.add(name)
    return {name: values for name, values in seen.items() if name in used}


def placeholder_for(value, secrets: dict):
    """The {{name}} of a saved credential this value is, or None."""
    for name, values in secrets.items():
        for secret in values:
            if not isinstance(secret, bool) and not isinstance(value, bool) and secret == value and len(str(secret)) >= 4:
                return "{{" + name + "}}"
    return None


def mask_text(text: str, secrets: dict) -> str:
    for name, values in secrets.items():
        for secret in values:
            if isinstance(secret, bool) or not isinstance(secret, (str, int, float)) or len(str(secret)) < 4:
                continue
            text = text.replace(str(secret), "{{" + name + "}}")
            if isinstance(secret, str):
                text = text.replace(json.dumps(secret)[1:-1], "{{" + name + "}}")
    for _name, pattern in MASKS:
        text = re.sub(pattern, MASKED, text)
    text = JSON_PAIR.sub(lambda m: m.group(0) if not telling(m.group(1)) or "{{" in m.group(3)
                         else f'"{m.group(1)}"{m.group(2)}"{MASKED}"', text)
    return PAIR.sub(lambda m: m.group(0) if not telling(m.group(1)) or "{{" in m.group(2)
                    else f"{m.group(1)}={MASKED}", text)


def mask_named(value, secrets: dict, under=False, parent=None):
    """Walk parsed JSON. A leaf under a name that says it is a credential is masked whole."""
    if isinstance(value, dict):
        return {k: mask_named(v, secrets, under or telling(k) or telling(k, parent), k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_named(v, secrets, under, parent) for v in value]
    if isinstance(value, bool) or value in (None, ""):
        return value
    known = placeholder_for(value, secrets)
    if known or (isinstance(value, str) and WHOLE.match(value)):
        return known or value
    if under:
        return MASKED
    return mask_text(value, secrets) if isinstance(value, str) else value


def scrub(value, secrets: dict):
    """What may be written into a card or printed: credentials become {{name}} or [masked], the rest stays."""
    if isinstance(value, str):
        return mask_text(value, secrets)
    if isinstance(value, list):
        return [scrub(v, secrets) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key == "headers" and isinstance(item, dict):
            out[key] = {}
            for name, held in item.items():
                shown = mask_text(str(held), secrets)
                out[key][name] = shown if "{{" in shown or not telling(name) else MASKED
        elif key == "body" and isinstance(item, str):
            try:
                parsed = json.loads(item)
                shown = json.dumps(mask_named(parsed, secrets)) if isinstance(parsed, (dict, list)) else mask_text(item, secrets)
            except (ValueError, RecursionError):     # not JSON, cut short, or nested too deep to walk
                shown = mask_text(item, secrets)
            out[key] = shown if len(shown) <= KEPT else shown[:KEPT - 1] + "\u2026"
        elif key == "json":
            out[key] = mask_named(item, secrets)
        else:
            out[key] = scrub(item, secrets)
    return out


def run_steps(base_url: str, steps: list, variables: dict, trail: list) -> list[str]:
    for number, step in enumerate(steps or [], 1):
        request = fill(step.get("request") or {}, variables)
        response = send(base_url, request)
        trail.append({"request": step.get("request") or {}, "response": response.brief()})
        problems = judge(response, fill(step.get("expect"), variables))
        if problems:
            return [f"step {number} {request.get('method', 'GET')} {request.get('path')}: {p}" for p in problems]
        for name, path in (step.get("save") or {}).items():
            value = pick(response.json, path) if response.json not in (MISSING, None) else MISSING
            if value is MISSING:
                return [f"step {number}: nothing at {path} to save as {name}"]
            variables[name] = value
    return []


def run_burst(base_url: str, burst: dict, variables: dict, trail: list) -> list[str]:
    if burst.get("requests"):
        requests = [fill(r, dict(variables, i=i, i1=i + 1)) for i, r in enumerate(burst["requests"])]
    else:
        requests = [fill(burst.get("request") or {}, dict(variables, i=i, i1=i + 1)) for i in range(int(burst.get("n", 0)))]
    barrier, out, unreachable, broken = threading.Barrier(len(requests)), [None] * len(requests), [], []

    def work(i):
        # Released first, connected after: a connection held open while the others gather would
        # occupy a worker of a correct service and make it look broken.
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            broken.append(i)
            return
        try:
            out[i] = send(base_url, requests[i])
        except Unreachable as exc:
            unreachable.append(str(exc))
            out[i] = Response(0, {}, b"", error=str(exc))
        except Exception as exc:                # a thread must never die silently
            out[i] = Response(0, {}, b"", error=f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=work, args=(i,)) for i in range(len(requests))]
    [t.start() for t in threads]
    [t.join() for t in threads]
    if broken:
        raise Unusable("the burst could not be released together; nothing was judged")
    if len(unreachable) == len(requests):
        raise Unreachable(unreachable[0])
    tally: dict[str, int] = {}
    for r in out:
        tally[str(r.status)] = tally.get(str(r.status), 0) + 1
    trail.append({"burst": burst.get("requests") or {"n": len(requests), "request": burst.get("request")},
                  "tally": dict(sorted(tally.items())),
                  "responses": [r.brief() for r in out]})
    expect, problems = fill(burst.get("expect") or {}, variables), []
    if expect.get("no_5xx", True):
        bad = sum(n for s, n in tally.items() if s == "0" or s.startswith("5"))
        if bad:
            problems.append(f"burst: {bad} transport error(s) or 5xx under load, tally {dict(sorted(tally.items()))}")
    for value in [v for name in ("status_counts", "max_status_counts", "min_status_counts")
                  for v in (expect.get(name) or {}).values()] + [
                  (expect.get("values_are_range") or {}).get(k) for k in ("from", "to") if "values_are_range" in expect]:
        if isinstance(value, bool) or not isinstance(value, int):
            raise Unusable(f"a number is asked where the list, once filled in, holds {value!r}")
    if "status_counts" in expect:
        want = {str(k): int(v) for k, v in expect["status_counts"].items()}
        if want != tally:
            problems.append(f"burst: expected statuses {want}, observed {dict(sorted(tally.items()))}")
    for status, most in (expect.get("max_status_counts") or {}).items():
        if tally.get(str(status), 0) > int(most):
            problems.append(f"burst: expected at most {most} of {status}, observed {tally.get(str(status), 0)}")
    for status, least in (expect.get("min_status_counts") or {}).items():
        if tally.get(str(status), 0) < int(least):
            problems.append(f"burst: expected at least {least} of {status}, observed {tally.get(str(status), 0)}")
    good = [r for r in out if 200 <= r.status < 300 and r.json not in (MISSING, None)]
    if not good and any(name in expect for name in ("distinct", "values_are_range", "identical_bodies")):
        problems.append(f"burst: no successful response to compare, tally {dict(sorted(tally.items()))}")
    if "distinct" in expect:
        values = [pick(r.json, expect["distinct"]) for r in good]
        if len({json.dumps(v, sort_keys=True, default=str) for v in values}) != len(values):
            problems.append(f"burst: values at {expect['distinct']} repeat: {sorted(map(str, values))}")
    if "values_are_range" in expect:
        spec = expect["values_are_range"]
        values = sorted(v for v in (pick(r.json, spec["path"]) for r in good) if isinstance(v, int))
        want = list(range(int(spec["from"]), int(spec["to"]) + 1))
        if values != want:
            problems.append(f"burst: expected each of {spec['from']}..{spec['to']} once at {spec['path']}, observed {values}")
    if expect.get("identical_bodies"):
        bodies = {json.dumps(r.json, sort_keys=True) for r in good}
        if len(bodies) > 1:
            problems.append(f"burst: expected identical bodies, observed {len(bodies)} different ones")
    return problems


def run_entry(base_url: str, entry: dict, repeat: int, seen: dict | None = None) -> tuple[list[str], list]:
    """Problems (empty means pass) and the trail of what was sent and seen. `seen` collects every
    value saved from a response, so that it can be masked wherever it is written."""
    rounds = max(1, repeat) if entry.get("burst") else 1   # a race that did not open proves nothing
    trail: list = []
    for _ in range(rounds):
        variables: dict = Remembering(seen if seen is not None else {})
        problems = run_steps(base_url, entry.get("setup"), variables, trail)
        if not problems and entry.get("burst"):
            problems = run_burst(base_url, entry["burst"], variables, trail)
        if not problems:
            problems = run_steps(base_url, entry.get("steps"), variables, trail)
        if not problems:
            problems = [f"read-back {p}" for p in run_steps(base_url, entry.get("readback"), variables, trail)]
        if problems:
            return problems, trail
    return [], trail


# ---------------------------------------------------------------- the check list
def check_matcher(where: str, path, want) -> list[str]:
    if not (isinstance(want, dict) and want and set(want) <= MATCHERS):
        return []
    problems = []
    if "exists" in want and not isinstance(want["exists"], bool):
        problems.append(f"{where}: {path}: exists must be true or false, without quotes")
    if "type" in want and want["type"] not in TYPES:
        problems.append(f"{where}: {path}: unknown type {want['type']!r}; the types are {', '.join(TYPES)}. "
                        f'To compare with an object, write {{"eq": {{...}}}}')
    if "regex" in want:
        try:
            re.compile(str(want["regex"]))
        except re.error as exc:
            problems.append(f"{where}: {path}: the pattern does not compile ({exc})")
    if "len" in want and (isinstance(want["len"], bool) or not isinstance(want["len"], int)):
        problems.append(f"{where}: {path}: len must be a whole number")
    if "in" in want and not isinstance(want["in"], list):
        problems.append(f"{where}: {path}: in must be a list")
    return problems


def check_request(where: str, request) -> list[str]:
    if not isinstance(request, dict):
        return [f"{where}: request must be an object"]
    problems = [f"{where}: request has an unknown field {k!r}" for k in request if k not in REQUEST_KEYS]
    path = request.get("path")
    if not isinstance(path, str) or not path.startswith("/") or re.search(r"[\s\x00-\x1f]", path):
        problems.append(f"{where}: path must start with / and hold no space; write a space as %20")
    headers = request.get("headers", {})
    if not isinstance(headers, dict):
        problems.append(f"{where}: headers must be an object")
    else:
        problems += [f"{where}: header {k!r} holds a line break" for k, v in headers.items()
                     if re.search(r"[\r\n]", f"{k}{v}")]
    return problems


def check_expect(where: str, expect, allowed: set, needed: bool) -> list[str]:
    if expect is None:
        return [f"{where}: lacks expect, so it would pass whatever the service answers"] if needed else []
    if not isinstance(expect, dict):
        return [f"{where}: expect must be an object"]
    problems = [f"{where}: expect has an unknown field {k!r}; the fields are {', '.join(sorted(allowed))}"
                for k in expect if k not in allowed]
    for name in ("json", "header", "testids", "body_contains", "status_counts", "max_status_counts", "min_status_counts"):
        if name in expect and expect[name] in ({}, [], None, ""):
            problems.append(f"{where}: {name} is empty, so it checks nothing")
    for name in ("testids", "body_contains"):
        if isinstance(expect.get(name), list) and any(not isinstance(v, str) or not v.strip() for v in expect[name]):
            problems.append(f"{where}: {name} holds an empty text, which every answer contains")
    for name, pattern in (expect.get("header") or {}).items() if isinstance(expect.get("header"), dict) else ():
        try:
            own = tuple(re.findall(r"[A-Za-z0-9=-]{3,}", re.sub(r"\\.", " ", str(pattern))))   # the words the pattern names
            if all(re.search(str(pattern), probe) for probe in PROBES + own):
                problems.append(f"{where}: the pattern for header {name} matches any value, so it checks nothing; "
                                f"write .+ to ask only that the header is there")
        except re.error as exc:
            problems.append(f"{where}: the pattern for header {name} does not compile ({exc})")
    for name in ("distinct",):
        if name in expect and not (isinstance(expect[name], str) and PATH.match(expect[name].strip())):
            problems.append(f"{where}: {name} must be a path such as $.id")
    for name in ("body_empty", "identical_bodies"):
        if name in expect and expect[name] is not True:
            problems.append(f"{where}: {name} can only be true; leave it out otherwise")
    if needed and not (set(expect) & allowed - {"no_5xx"}) and expect.get("no_5xx", True) is not True:
        problems.append(f"{where}: expect holds nothing to check")
    elif needed and not (set(expect) & allowed):
        problems.append(f"{where}: expect holds nothing to check")
    if "status" in expect:
        statuses = expect["status"] if isinstance(expect["status"], list) else [expect["status"]]
        if not statuses or any(isinstance(s, bool) or not isinstance(s, int) for s in statuses):
            problems.append(f"{where}: status must be a number or a list of numbers")
    if "json" in expect:
        if not isinstance(expect["json"], dict):
            problems.append(f"{where}: expect.json must map a path to a value or a matcher")
        else:
            for path, want in expect["json"].items():
                if not PATH.match(str(path).strip()):
                    problems.append(f"{where}: {path!r} is not a path; write it as $.name, $.list[0].name or $")
                problems += check_matcher(where, path, want)
    for name in ("testids", "body_contains"):
        if name in expect and not isinstance(expect[name], list):
            problems.append(f"{where}: {name} must be a list")
    return problems


def check_steps(where: str, steps, needed: bool) -> list[str]:
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        return [f"{where} must be a list of steps, each an object"]
    problems = []
    for number, step in enumerate(steps, 1):
        here = f"{where} step {number}"
        problems += [f"{here}: unknown field {k!r}" for k in step if k not in STEP_KEYS]
        problems += check_request(here, step.get("request"))
        if not needed and "expect" not in step and not step.get("save"):
            problems.append(f"{here}: a setup step needs expect or save, or nobody learns that it failed")
        problems += check_expect(here, step.get("expect"), EXPECT_KEYS, needed)
        if "save" in step and not (isinstance(step["save"], dict) and step["save"]
                                   and all(isinstance(v, str) and PATH.match(v.strip()) for v in step["save"].values())):
            problems.append(f"{here}: save must map a name to a path such as $.id")
    return problems


def later(value) -> bool:
    """A text that is only {{name}}: its value is known when the entry runs."""
    return isinstance(value, str) and bool(WHOLE.match(value))


def check_burst(where: str, burst, has_readback: bool) -> list[str]:
    if not isinstance(burst, dict):
        return [f"{where}: burst must be an object"]
    problems = [f"{where}: burst has an unknown field {k!r}" for k in burst if k not in BURST_KEYS]
    if "requests" in burst:
        if not isinstance(burst["requests"], list) or not burst["requests"]:
            return problems + [f"{where}: burst.requests must be a list with at least one request"]
        count = len(burst["requests"])
        for request in burst["requests"]:
            problems += check_request(f"{where} burst", request)
    else:
        count = burst.get("n")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            return problems + [f"{where}: burst.n must be a whole number of at least 1"]
        problems += check_request(f"{where} burst", burst.get("request"))
    if count > MOST_IN_FLIGHT:
        problems.append(f"{where}: a burst of {count} is more than the {MOST_IN_FLIGHT} in flight the service is judged under")
    problems += check_expect(f"{where} burst", burst.get("expect"), BURST_EXPECT_KEYS, not has_readback)
    expect = burst.get("expect") if isinstance(burst.get("expect"), dict) else {}
    spec = expect.get("values_are_range")
    if spec is not None:
        whole = lambda v: (isinstance(v, int) and not isinstance(v, bool)) or later(v)            # noqa: E731
        if not (isinstance(spec, dict) and {"path", "from", "to"} <= set(spec) and isinstance(spec["path"], str)
                and PATH.match(spec["path"].strip()) and whole(spec["from"]) and whole(spec["to"])):
            problems.append(f"{where}: values_are_range needs a path and whole numbers from and to")
        elif not later(spec["from"]) and not later(spec["to"]) and spec["from"] > spec["to"]:
            problems.append(f"{where}: values_are_range runs from {spec['from']} to {spec['to']}: an empty range checks nothing")
    for name in ("status_counts", "max_status_counts", "min_status_counts"):
        if name in expect and not isinstance(expect[name], dict):
            problems.append(f"{where}: {name} must map a status to a count")
            continue
        for status, number in (expect.get(name) or {}).items():
            if later(number):
                continue                    # a saved value: it is a number only once it is filled in
            if isinstance(number, bool) or not isinstance(number, int) or number < 0:
                problems.append(f"{where}: {name} for {status} must be a whole number")
            elif name == "min_status_counts" and number == 0:
                problems.append(f"{where}: at least 0 of {status} is always true")
            elif name == "max_status_counts" and number >= count:
                problems.append(f"{where}: at most {number} of {status} among {count} requests is always true")
    return problems


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield str(k)
            yield from strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from strings(v)


def saved_by(entry: dict) -> set:
    names = {"i", "i1"}
    for block in ("setup", "steps", "readback"):
        for step in entry.get(block) or []:
            if isinstance(step, dict) and isinstance(step.get("save"), dict):
                names |= set(step["save"])
    return names


def numbers_asked(entry: dict):
    """Every value that stands where a number is asked, in the burst of the entry."""
    burst = entry.get("burst") if isinstance(entry.get("burst"), dict) else {}
    expect = burst.get("expect") if isinstance(burst.get("expect"), dict) else {}
    spec = expect.get("values_are_range") if isinstance(expect.get("values_are_range"), dict) else {}
    yield from (spec.get("from"), spec.get("to"))
    for name in ("status_counts", "max_status_counts", "min_status_counts"):
        if isinstance(expect.get(name), dict):
            yield from expect[name].values()


def check_entry(entry: dict, where: str, replayed: bool = False) -> list[str]:
    """Every reason this entry cannot be run as written. Empty means usable. `replayed` says that the
    entry comes from a card: that a burst needs a read-back is a rule for a list, and a card that an
    earlier run wrote has to close the way every card closes."""
    problems = [f"{where}: unknown field {k!r}; the fields are {', '.join(sorted(ENTRY_KEYS))}"
                for k in entry if k not in ENTRY_KEYS]
    for value in numbers_asked(entry):
        if later(value) and WHOLE.match(value).group(1) not in saved_by(entry):
            problems.append(f"{where}: {value} stands where a number is asked, and no step of the entry saves it")
    shapes = sorted({name for text in strings(entry) for name, pattern in MASKS if re.search(pattern, text)})
    if shapes:
        problems.append(f"{where}: holds a value shaped like a credential ({', '.join(shapes)}), and a public repository "
                        f"is scanned for those. For a real one, save it from a response and write {{{{name}}}}. "
                        f"For a made-up one, keep it under 20 characters or without a digit")
    for field in ("id", "sentence", "kind"):
        if not isinstance(entry.get(field), str) or not entry[field].strip():
            problems.append(f"{where}: {field} must be text")
    if entry.get("kind") not in KINDS:
        problems.append(f"{where}: kind must be one of {KINDS}")
    runnable = entry.get("steps") or entry.get("burst")
    if not runnable and not str(entry.get("unchecked") or "").strip():
        problems.append(f"{where}: needs steps or a burst, or `unchecked` with the reason it cannot be checked")
    if "no_readback" in entry:
        reason = entry["no_readback"]
        if not isinstance(reason, str) or len([w for w in reason.split() if re.search(r"[^\W\d_]", w)]) < MIN_WORDS:
            problems.append(f"{where}: no_readback must give the reason in words, {MIN_WORDS} or more: "
                            f"why nothing can be read after the burst")
        if entry.get("readback"):
            problems.append(f"{where}: holds a readback and no_readback; keep the one that is true")
        if not entry.get("burst"):
            problems.append(f"{where}: no_readback is for a burst, and this entry holds none")
    elif entry.get("kind") == "concurrency" and entry.get("burst") and not entry.get("readback") and not replayed:
        # A burst of another kind loads as it did before this rule: the coverage line names it.
        problems.append(f"{where}: an entry of kind concurrency that holds a burst needs a readback, which reads the "
                        f"state afterwards through the public interface, or no_readback with the reason in words. "
                        f"Status codes say what the service claimed; the read-back says what it did")
    for name, needed in (("setup", False), ("steps", True), ("readback", True)):
        if name in entry:
            problems += check_steps(f"{where} {name}", entry[name], needed)
    if "burst" in entry:
        problems += check_burst(where, entry["burst"], bool(entry.get("readback")))
    return problems


def load(path: str) -> tuple[list[dict], list[str], list[str]]:
    """The entries, where each one stands in the list, and every reason the list cannot be used."""
    entries, wheres, problems, seen = [], [], [], set()
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return [], [], [f"could not read {path}: {exc}"]
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except (ValueError, RecursionError) as exc:
            problems.append(f"line {number}: not JSON ({exc})")
            continue
        if not isinstance(entry, dict):
            problems.append(f"line {number}: an entry is a JSON object")
            continue
        name = entry.get("id") if isinstance(entry.get("id"), str) else "?"
        where = f"line {number} ({name})"
        problems += check_entry(entry, where)
        if name != "?" and name in seen:
            problems.append(f"{where}: id repeats")
        seen.add(name)
        entries.append(entry)
        wheres.append(where)
    if not entries and not problems:
        problems.append("the check list is empty")
    return entries, wheres, problems


def warnings_for(entries: list[dict]) -> list[str]:
    return [f"warning: {entry.get('id')}: holds NAME=value with a name such as token or password. A card written from "
            f"this entry will be reported as a credential by the organizers' checker, because a card is a .json file. "
            f"Send the value as JSON or in a header where the requirements allow it"
            for entry in entries if any(ASSIGNED.search(text) for text in strings(entry))]


# ---------------------------------------------------------------- the requirements files
def one_line(text) -> str:
    """Free text as it is stored and printed: on one line, so that it cannot pass for lines of this tool."""
    return " ".join(str(text).split())


def printed(text: str, most: int, last: bool = False) -> str:
    """A piece of a sentence as it is printed: masked, cut to `most` characters, in quotation marks."""
    text = mask_text(text.strip(), {})
    if len(text) > most:
        text = CUT + text[1 - most:] if last else text[:most - 1] + CUT
    return json.dumps(text, ensure_ascii=False)


def plain_lines(lines: list[str]) -> list[str]:
    """Each line as it is compared: markers, emphasis marks and back-ticks left out, white space
    collapsed. A number that starts a line is text when the line goes on with a paragraph, such as a
    status at the start of a wrapped line: Markdown opens a list inside a paragraph with 1 only."""
    out, listed, gap, text_at = [], False, True, 0
    for line in lines:
        found = MARKER.match(line)
        number = found.group("number")
        # Inside a list a number opens the next item, unless it stands where the text of the item stands.
        if number and int(number) != 1 and not gap and (not listed or found.start("number") >= text_at):
            out.append(one_line(EMPHASIS.sub("", line[found.start("number"):])))
            continue
        out.append(one_line(EMPHASIS.sub("", line[found.end():])))
        if NO_PROSE.match(line):
            gap, listed = True, listed and not out[-1]      # a list goes on after an empty line
            continue
        if number or found.group("bullet"):
            listed, text_at = True, found.end()
        elif gap and not re.match(r"\s*(?:>\s*)*(?:\t| {2})", line):
            listed = False                      # a paragraph of its own, not one that belongs to a list item
        gap = False
    return out


def plain(text: str) -> str:
    """A sentence as it is compared."""
    return " ".join(filter(None, plain_lines(text.splitlines())))


class Requirements:
    """One requirements file: its text as it is compared, and where each of its lines stands in it."""

    def __init__(self, name: str):
        self.name, self.lines, self.quoted, parts, length = name, [], [], [], 0
        try:
            # Read without the byte order mark, which is no part of the first sentence.
            raw = pathlib.Path(name).read_text(encoding="utf-8-sig") if str(name).strip() else None
        except (OSError, UnicodeDecodeError) as exc:
            raise Unusable(f"could not read {name}: {exc}") from exc
        if raw is None:
            raise Unusable("--spec was given an empty name where a requirements file is asked")
        for number, shown in enumerate(plain_lines(raw.splitlines()), 1):
            if shown:
                self.lines.append((length, length + len(shown), number))
                parts.append(shown)
                length += len(shown) + 1
        self.text = " ".join(parts)
        self.starts = [start for start, _end, _number in self.lines]
        self.edges = set(self.starts), {end for _start, end, _number in self.lines}

    def places(self, sentence: str) -> list[tuple[int, int, str]]:
        """(start, end, fault) for every place the sentence stands as whole words. The fault is empty when
        the place quotes one requirement: it begins where a sentence, a line or a table cell begins, ends
        where one ends, and runs over no more lines than one requirement may take. A sentence also
        begins after a label, a bracket or a quotation mark, and ends before the mark that closes it."""
        found, at, said = [], self.text.find(sentence) if sentence else -1, False
        while at != -1:
            end = at + len(sentence)
            before, after = self.text[max(0, at - 1):at], self.text[end:end + 1]
            if not (before.isalnum() and sentence[0].isalnum()) and not (after.isalnum() and sentence[-1].isalnum()):
                first, last = bisect.bisect_right(self.starts, at) - 1, bisect.bisect_right(self.starts, end - 1) - 1
                # What stands beside it in its lines, as far as it is read and printed: a line may be long.
                lead = self.text[max(self.lines[first][0], at - 2 * SHOWN):at]
                tail = self.text[end:min(self.lines[last][1], end + 2 * SHOWN)]
                # Nothing before it, or nothing after it, is an edge as well.
                begins = (at in self.edges[0] or self.text[max(0, at - 2):at].strip()[-1:] in SENTENCE_ENDS
                          or bool(OPENED.search(self.text[max(0, at - 3):at]) or LABEL.fullmatch(lead.strip())))
                ends = (end in self.edges[1] or sentence[-1] in SENTENCE_ENDS
                        or self.text[end:end + 2].strip()[:1] in SENTENCE_ENDS
                        or bool(CLOSED.match(self.text[end:end + 3])))
                lines = last - first + 1
                fault = "" if begins and ends else "stands in the requirements only as part of a longer sentence"
                if fault and not said:
                    # Said for the first such place, with what stands beside it in its own sentence, so
                    # that the seat sees why.
                    lead = re.split(r"[.!?:;|](?:\s|$)", lead)[-1]
                    tail = re.match(r".*?[.!?:;|](?=\s|$)|.*", tail).group(0)
                    beside = ([] if begins else [f"{printed(lead, SHOWN, last=True)} before it"]) + (
                        [] if ends else [f"{printed(tail, SHOWN)} after it"])
                    fault += f": line {self.lines[first][2]} of {self.name} has {' and '.join(beside)}"
                    said = True
                if lines > MOST_LINES:
                    fault = (f"runs over {lines} lines of {self.name}, more than the {MOST_LINES} that one "
                             f"requirement may take")
                found.append((at, end, fault))
            at = self.text.find(sentence, at + 1)
        return found

    def left(self) -> tuple[list[int], int]:
        """The numbers of the lines that hold a requirement word outside every quoted sentence, and how
        many lines hold a requirement word at all. A word belongs to the line it starts on."""
        holding, left = set(), set()
        for word in REQUIREMENT.finditer(self.text):
            number = self.lines[bisect.bisect_right(self.starts, word.start()) - 1][2]
            holding.add(number)
            if not any(start <= word.start() and word.end() <= end for start, end in self.quoted):
                left.add(number)
        return sorted(left), len(holding)


def look_up(entries: list[dict], wheres: list[str], names: list[str]) -> tuple[list[str], list | None, str, list[str]]:
    """The sentences of the list, looked up in the requirements files: the problems (a file that cannot be
    read, a sentence that stands in none of them), the sentence each entry quotes as it was compared
    (None when no file was given), how the coverage line ends, and the lines after it."""
    if not names:
        return [], None, "the sentences were not checked against a requirements file (give --spec FILE)", []
    files, problems, partly, quotes, whole = [], [], [], [], []
    once = {}
    for name in names:                          # a file named twice, also under two spellings, is read once
        once.setdefault(str(pathlib.Path(name).resolve()), name)
    for name in once.values():
        try:
            files.append(Requirements(name))
        except Unusable as exc:
            problems.append(str(exc))
    if problems:
        return problems, None, "", []
    found = {f.name: 0 for f in files}
    for entry, where in zip(entries, wheres):
        if not isinstance(entry.get("sentence"), str) or not entry["sentence"].strip():
            continue                            # said already, when the list was loaded
        quote = plain(entry["sentence"])
        places = [(f, f.places(quote)) for f in files]
        first = next((f for f, stands in places if stands), None)
        if first is None:
            problems.append(f"{where}: its sentence stands in none of the requirements files: "
                            f"{printed(entry['sentence'], 120)}")
            continue
        quotes.append(quote)
        for f, stands in places:
            f.quoted += [(start, end) for start, end, fault in stands if not fault]
        faults = [fault for _f, stands in places for _start, _end, fault in stands]
        if not all(faults):
            found[first.name] += quote not in whole     # twenty entries that quote one sentence quote one
            whole.append(quote)
        if all(faults):
            partly.append(f"note: {entry.get('id')}: its sentence {faults[0]}, so it counts as quoting no line. "
                          f"Quote one whole sentence for one entry")
    if problems:
        return problems + [f"the requirements files that were read: {', '.join(found)}. Quote each requirement word for "
                           f"word: a sentence that the requirements do not hold cannot be counted as covered"], None, "", []
    parts, unquoted, of = [], 0, 0
    for f in files:
        left, holding = f.left()
        unquoted, of = unquoted + len(left), of + holding
        more = f" and {len(left) - NAMED} more" if len(left) > NAMED else ""
        parts.append(f"{f.name}: {len(left)} of {holding} lines with a requirement word hold one that no entry quotes"
                     + (f", lines {', '.join(map(str, left[:NAMED]))}{more}" if left else ""))
    second = f"not quoted: {'; '.join(parts)}. A requirement word is one of: {', '.join(REQUIREMENT_WORDS)}"
    # The measurement stands in the coverage line too: that line is the one a seat is asked to post.
    # A part of a sentence was found, which keeps the list usable, and quotes nothing: the two are told apart.
    one, by = len(set(whole)) == 1, "1 entry" if len(whole) == 1 else f"{len(whole)} entries"
    parts_only = ("" if not partly else "; 1 entry quotes only part of a sentence" if len(partly) == 1
                  else f"; {len(partly)} entries quote only part of a sentence")
    ending = (f"{unquoted} of {of} requirement lines are quoted by no entry; {len(set(whole))} "
              f"{'sentence of the requirements is' if one else 'sentences of the requirements are'} quoted by {by}"
              f"{parts_only}: " + ", ".join(f"{name} ({count})" for name, count in found.items()))
    return [], quotes, ending, [second] + partly


def accepted(path: str | None) -> tuple[dict | None, list[str]]:
    """The verdict card of the revision to be checked, or the reason it cannot be taken. No flag is
    None; an empty name is a flag, given by a variable that held nothing."""
    if path is None:
        return None, []
    if not path.strip():
        return None, ["--accepted was given an empty name where the file of the verdict card is asked, so no "
                      "revision could be taken: nothing was sent"]
    try:
        c = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        return None, [f"could not read {path}: {exc}"]
    if not isinstance(c, dict) or c.get("card") != "verdict":
        return None, [f"{path} is not a verdict card; --accepted takes the card the revision check wrote"]
    bad = cards.validate(c)
    if bad:
        return None, [f"{path} is not a valid verdict card: {'; '.join(bad)}"]
    if not isinstance(c["revision"], str) or not c["revision"].strip():
        return None, [f"{path} does not name its revision as text"]
    if re.search(r"\s", c["revision"]):
        return None, [f"{path} names a revision that holds white space, {json.dumps(c['revision'])}. A revision is one "
                      f"word, such as the full commit id: nothing was sent"]
    if c["verdict"] != "ACCEPT":
        return None, [f"{path} holds the verdict {c['verdict']} on revision {c['revision']}. Only an accepted revision "
                      f"is checked: nothing was sent"]
    return c, []


def address(base_url: str) -> list[str]:
    url = urllib.parse.urlsplit(base_url)
    if url.scheme not in ("http", "https") or not url.hostname:
        return [f"{base_url} is not an address this tool can call; write it as http://host:port"]
    return []


def coverage_line(entries: list[dict], quotes: list | None, ending: str) -> str:
    """How many sentences have an entry that can run, and `ending`: whether the sentences were found in
    the requirements. Entries that quote the same sentence count as one sentence. When no requirements
    file was read, `quotes` is None and every entry counts as a sentence of its own."""
    quotes = [e["id"] for e in entries] if quotes is None else quotes
    unchecked = [e["id"] for e in entries if not (e.get("steps") or e.get("burst"))]
    unread = [e["id"] for e in entries if e.get("burst") and not e.get("readback")]
    checked = {quote for e, quote in zip(entries, quotes) if e.get("steps") or e.get("burst")}
    line = f"coverage: {len(checked)} of {len(set(quotes))} sentences have an executable check"
    line += f"; unchecked: {', '.join(unchecked)}" if unchecked else ""
    line += f"; no read-back after the burst: {', '.join(unread)}" if unread else ""
    return f"{line}; {ending}"


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def finding(entry: dict, problems: list[str], trail: list, args, seen: dict, before: list | None = None) -> dict:
    """The card of a failed entry. `before` holds the withdrawals of the card that this one is written over."""
    replay = {k: entry[k] for k in ("setup", "steps", "burst", "readback", "no_readback") if entry.get(k)}
    secrets = secrets_of(entry, seen)
    return {"card": "finding", "stage": args.stage, "from": args.sender, "to": args.to,
            **({"revision": args.revision} if args.revision else {}), "check_id": entry["id"],
            "sentence": entry["sentence"], "kind": entry["kind"], "replay": replay,
            "expected": "every expectation in the replay holds", "observed": scrub("; ".join(problems), secrets),
            "status": "open", "at": now(), **({"withdrawn_before": before} if before else {}),
            "trail": scrub(trail[-3:], secrets)}


def withdrawals(path: pathlib.Path, check_id: str) -> tuple[list[dict], bool]:
    """The withdrawals that the card of this check holds in the folder, oldest first, and whether the
    card is withdrawn now. A file that is no finding card of this check holds none."""
    try:
        c = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return [], False
    if not isinstance(c, dict) or c.get("card") != "finding" or c.get("check_id") != check_id:
        return [], False

    def kept(w) -> dict | None:             # a withdrawal that names no reason says nothing, and is not carried
        if not isinstance(w, dict) or not isinstance(w.get("reason"), str) or not w["reason"].strip():
            return None
        at = {"at": one_line(w["at"])} if isinstance(w.get("at"), str) and w["at"].strip() else {}
        return {"reason": mask_text(one_line(w["reason"]), {}), **at}

    before = [kept(w) for w in (c["withdrawn_before"] if isinstance(c.get("withdrawn_before"), list) else [])]
    last = None
    if c.get("status") == "withdrawn":
        last = kept({"reason": c.get("withdrawn_reason"), "at": c.get("withdrawn_at")})
    return [w for w in before + [last] if w], bool(last)


def card_name(check_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", check_id)
    if safe != check_id:                    # two ids must never share a file
        safe += "-" + hashlib.sha1(check_id.encode()).hexdigest()[:8]
    return safe + ".json"


def cmd_run(args) -> int:
    entries, wheres, problems = load(args.checks)
    problems += address(args.base_url)
    if not problems and not any(e.get("steps") or e.get("burst") for e in entries):
        problems.append("no entry can be run: every sentence is listed as unchecked, so nothing was checked")
    verdict, refused = accepted(args.accepted)
    unfound, quotes, ending, after = look_up(entries, wheres, args.spec)
    problems += refused + unfound
    if problems:
        for p in problems:
            print(p)
        return 2
    args.revision = verdict["revision"] if verdict else ""
    if verdict:
        print(f"revision {args.revision}: accepted by {one_line(verdict['from'])} for stage {verdict['stage']}, "
              f"card {args.accepted}" + (f". This run is for stage {args.stage}." if verdict["stage"] != args.stage else ""))
    for warning in warnings_for(entries):
        print(warning)
    failed = passed = unchecked = 0
    written = []
    folder = pathlib.Path(args.cards) if args.cards else None

    def kept() -> str:
        return (f" {len(written)} card(s) already written are kept: {', '.join(written)}." if written
                else " No finding was written.")

    try:
        for entry in entries:
            if not (entry.get("steps") or entry.get("burst")):
                unchecked += 1
                print(f"UNCHECKED {entry['id']}: {entry['unchecked']}")
                continue
            seen: dict = {}
            found, trail = run_entry(args.base_url, entry, args.repeat, seen)
            if not found:
                passed += 1
                unread = f" (no read-back: {one_line(entry['no_readback'])})" if entry.get("no_readback") else ""
                print(f"PASS {entry['id']}{unread}")
                continue
            failed += 1
            print(f"FAIL {entry['id']}: {scrub('; '.join(found), secrets_of(entry, seen))}")
            if folder:
                # A card is written over the one that is there. What says that the check was taken back
                # once is carried into the new card: a finding is never deleted, and neither is that.
                before, withdrawn = withdrawals(folder / card_name(entry["id"]), entry["id"])
                if withdrawn:
                    on = f" on {before[-1]['at']}" if "at" in before[-1] else ""
                    print(f"{entry['id']} was withdrawn{on} ({before[-1]['reason']}) and fails again. Its card is open "
                          f"again and keeps the withdrawal under withdrawn_before. Correct the entry, or take it out "
                          f"of the list")
                made = finding(entry, found, trail, args, seen, before)
                bad = cards.validate(made)
                if bad:
                    raise Unusable(f"the card for {entry['id']} is invalid: {bad}")
                folder.mkdir(parents=True, exist_ok=True)
                (folder / card_name(entry["id"])).write_text(json.dumps(made, indent=1), encoding="utf-8")
                written.append(card_name(entry["id"]))
                print(f"card written: {folder / card_name(entry['id'])}")
    except Unreachable as exc:
        print(f"could not reach {args.base_url}: {exc}. Start the service and run again.{kept()}")
        return 3
    except Unusable as exc:
        print(f"could not go on: {exc}.{kept()}")
        return 2
    except Exception as exc:                # an error in this tool is never a verdict on the service
        print(f"could not go on: {type(exc).__name__}: {exc}.{kept()}")
        return 2
    for line in [coverage_line(entries, quotes, ending)] + after:
        print(line)
    print(f"{passed} passed, {failed} failed, {unchecked} unchecked")
    return 1 if failed else 0


def cmd_replay(args) -> int:
    path = pathlib.Path(args.card)
    try:
        c = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"could not read {path}: {exc}")
        return 2
    bad = cards.validate(c)
    if bad or c.get("card") != "finding":
        print(f"{path} is not a valid finding card: {bad or 'wrong card type'}")
        return 2
    if c["status"] == "withdrawn":
        print(f"{path} was withdrawn and cannot be replayed: {one_line(c['check_id'])} "
              f"({one_line(c['withdrawn_reason'])}). The card stays as it is.")
        return 2
    entry = dict(c["replay"], id=c["check_id"], sentence=c["sentence"], kind=c["kind"])
    unusable = check_entry(entry, "the replay", replayed=True) + address(args.base_url)
    if unusable:
        print(f"{path} cannot be replayed, and stays as it is:")
        for p in unusable:
            print(f"  {p}")
        return 2
    seen: dict = {}
    try:
        found, _trail = run_entry(args.base_url, entry, args.repeat, seen)
    except Unreachable as exc:
        print(f"could not reach {args.base_url}: {exc}. The card is unchanged.")
        return 3
    except Exception as exc:
        print(f"could not go on: {type(exc).__name__}: {exc}. The card is unchanged.")
        return 2
    if found:
        print(f"still failing {c['check_id']}: {scrub('; '.join(found), secrets_of(entry, seen))}")
        return 1
    c.update(status="closed", closed_at=now(), observed_on_close="every expectation in the replay held")
    path.write_text(json.dumps(c, indent=1), encoding="utf-8")
    print(f"card closed: {c['check_id']}")
    return 0


def cmd_withdraw(args) -> int:
    """Take back a check that was wrong. The card stays, with the reason: a finding is never deleted."""
    path = pathlib.Path(args.card)
    try:
        c = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        print(f"could not read {path}: {exc}")
        return 2
    bad = cards.validate(c)
    if bad or c.get("card") != "finding":
        print(f"{path} is not a valid finding card: {bad or 'wrong card type'}")
        return 2
    if c["status"] != "open":
        print(f"{path} is {c['status']} and cannot be withdrawn: {one_line(c['check_id'])}. Only an open card can be "
              f"withdrawn. The card stays as it is.")
        return 2
    reason = one_line(args.reason)          # line breaks and runs of spaces add no words
    if len(reason) < MIN_REASON:
        print(f"a reason shorter than {MIN_REASON} characters says nothing. Say in words why the check "
              f"{one_line(c['check_id'])} was wrong. The card stays as it is.")
        return 2
    if ASSIGNED.search(reason):
        print(f"the reason holds a name and a value joined by = with a name such as key, token or password. A card is a "
              f".json file, and a public repository is scanned for that shape, whatever the value is. Write the "
              f"reason without the = sign. The card stays as it is.")
        return 2
    # Masked as everything else in a card: the card is committed.
    c.update(status="withdrawn", withdrawn_reason=mask_text(reason, {}), withdrawn_at=now())
    path.write_text(json.dumps(c, indent=1), encoding="utf-8")
    print(f"card withdrawn: {one_line(c['check_id'])}")
    return 0


def cmd_coverage(args) -> int:
    entries, wheres, problems = load(args.checks)
    unfound, quotes, ending, after = look_up(entries, wheres, args.spec)
    if problems + unfound:
        for p in problems + unfound:
            print(p)
        return 2
    for line in [coverage_line(entries, quotes, ending)] + after:
        print(line)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="black-box checks from a per-sentence check list")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--checks", required=True)
    r.add_argument("--base-url", required=True)
    # --s was read as --stage while no other flag of a run began with s, and still is.
    r.add_argument("--stage", "--s", type=int, required=True)
    r.add_argument("--cards", default="")
    r.add_argument("--repeat", type=int, default=3, help="a burst must pass this many times in a row")
    r.add_argument("--from", dest="sender", default="verifier")
    r.add_argument("--to", default="coordinator")
    r.add_argument("--accepted", default=None, metavar="CARD.json",
                   help="the verdict card of the revision that is served; one that is not an ACCEPT is refused")
    p = sub.add_parser("replay")
    p.add_argument("card")
    p.add_argument("--base-url", required=True)
    p.add_argument("--repeat", type=int, default=3)
    c = sub.add_parser("coverage")
    c.add_argument("--checks", required=True)
    w = sub.add_parser("withdraw")
    w.add_argument("card")
    w.add_argument("--reason", required=True, help=f"why the check was wrong, in {MIN_REASON} characters or more")
    for command in (r, c):
        command.add_argument("--spec", action="extend", nargs="+", default=[], metavar="FILE",
                             help="the requirements files the quoted sentences must stand in: one file or several, "
                                  "and the flag may be given more than once")
    args = ap.parse_args(argv)
    commands = {"run": cmd_run, "replay": cmd_replay, "coverage": cmd_coverage, "withdraw": cmd_withdraw}
    try:
        return commands[args.command](args)
    except Exception as exc:                # an error in this tool is never a verdict on the service
        print(f"could not go on: {type(exc).__name__}: {exc}. That is an error of this tool, not a finding.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
