#!/usr/bin/env python3
"""Check a posted revision on trees its author cannot edit.

Given a revision, the base it was built on and the command that is claimed to pass,
this clones the repository into a temporary directory and:
  1. runs the command on the posted revision                      (must pass)
  2. runs it on the base with the delivered tests                 (must fail)
  3. runs it on the revision with the implementation reverted     (must fail)
  4. runs it on the revision with every function emptied          (must fail, unless every
     test the work wrote vouched for itself by failing in one of the runs above)
  5. runs it on the base as it was, and compares what the runner itself reports: fewer
     tests run, more tests left out, or no summary where the base printed one
  6. compares the test files with the base: deleted files, tests that were there and are
     gone, new skip markers, fewer assertions, test files or test configuration changed
     but not listed
  7. with a frozen reference, compares what each earlier test file declares with what it
     declared when it was accepted: a test that is gone, a test whose body changed or that
     is defined once more below, fewer assertions, more skip markers. Tests that were added
     are fine; a helper, a fixture or a table of cases that changed beside the tests is
     said in a note
  8. names what the work changed outside --subdir. A test file there that was changed or
     deleted is a finding, unless it has a --spec-change note, is the same as the file of
     its name in --subdir, or the ledger holds the revision as accepted for its stage
Everything between the base and the revision is judged, however many commits that is.
The base is the last accepted revision; while nothing has been accepted it is the revision
the work started from, or `empty` when there was nothing before the work. A rejected
revision is never a base, and neither is one that is built on it: what was rejected would
be forgiven. With --ledger the tool writes every verdict down, names the base and the
frozen tests itself, and refuses a base or frozen tests that are not the ones it holds.
Steps 2 and 3 run only when the work changes both test files and other files. When they
fail because the tests did not find the code they ask for, which is what happens when a
stage adds new modules, they show nothing and the verdict says so; when the base holds no
stage folder and the frozen tests are named, the delivered tests then also run once on the
earlier stage's accepted code, where the code they ask about exists as it was. Step 4 is
the run that does show it: every Python function the work changed or added returns nothing,
and the delivered tests have to notice. A delivered test vouches for itself and for no
other: one that is new or changed under its name, and that failed while it ran, by its own
name, on code without the work, may pass on emptied code, and the card names it and the
run that showed it. A test that failed in no such run keeps the finding, and a failure the
output itself puts down to absent code vouches for nothing.
Step 4 is judged as a whole. Which delivered tests are not among its failures is said in a
note; the verdict closes with what showed that the delivered tests can fail.
A card that stands at --out when the check begins is moved to <out>.earlier: it must not
pass for the verdict of this check, and a check that does not run could not write it again.
Every command runs inside --subdir, with no standard input. A command that changes into the
stage folder itself (`cd stage-1 && ...` beside --subdir stage-1) is refused: it would fail on
its first word, whatever the work is. Give the command as it is run from inside the folder.
The source repository's working tree is never touched.

usage: falsify.py --repo PATH --revision SHA --base SHA|empty --test-cmd "CMD" --stage N
                  [--subdir DIR] [--changed-tests a,b] [--frozen-ref SHA] [--frozen-dir DIR]
                  [--spec-change "path=note"]... [--out verdict.json] [--timeout 900]
                  [--ledger accepted.jsonl]     with an ACCEPT in the ledger, --base may be left out
                  a --subdir that ends in a number is the folder of that stage: --stage has to be that number
exit:  0 ACCEPT · 1 REJECT · 2 wrong arguments, or a base, frozen tests, a stage, a command, a
       place for the card or a ledger that is refused
       3 the check could not run (no verdict is written)
Standard library and git only.
"""
from __future__ import annotations

import argparse
import ast
import collections
import datetime
import json
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import warnings

import card as cards

# Test files. Everything under a tests folder counts, fixtures included, so every such change
# has to be listed.
TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs)/|(^|/)(test_[^/]+|[^/]+_test|conftest)\.py$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$|_test\.(go|rs|rb)$|(^|/)[^/]+Tests?\.(java|kt|cs)$|(^|/)[^/]+_spec\.rb$")
# The configuration that decides which tests run. It has to be listed like a test file, but it
# is not a delivered test: copying it onto the base proves nothing about the code.
CONFIG_PATH = re.compile(
    r"(^|/)(pytest\.ini|tox\.ini|\.mocharc\.[a-z]+|phpunit\.xml|(jest|vitest|playwright|karma)\.config\.[cm]?[jt]s)$")
# A way of switching a test off. The words skip, todo and only count in a call of the test
# functions, never alone: they are ordinary words in the code under test.
SKIP = re.compile(
    r"\bmark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail|importorskip)\(|@(unittest\.)?skip(If|Unless)?\b"
    r"|\bunittest\.skip\(|\bSkipTest\b|self\.skipTest\(|\bexpectedFailure\b|\b__test__\s*=\s*False\b"
    r"|\bcollect_ignore(_glob)?\b"
    r"|\b(?:test|it|describe|suite|context)(?:\.\w+)*\.(?:skip|skipIf|runIf|todo|only|fixme|failing)\b"
    r"|\b(?:test|it|describe)\s*\(\s*(?:'[^']*'|\"[^\"]*\"|`[^`]*`)\s*,\s*\{\s*(?:skip|todo)\s*:"
    r"|\bx(it|describe|test)\s*\(|@(Disabled|Ignore)\b|\bt\.Skip(f|Now)?\(|#\[ignore\]")
# The tests a file declares. Read are the files runners collect when nobody tells them otherwise, and
# every other code file under a tests folder when it holds a TestCase or the project widens what is
# collected. What keeps helpers out: shared fixture files are not read, and a name is dropped when it
# is a fixture, a parameter of another function, or called somewhere in its file, since nothing calls
# a test by its name.
NOT_COLLECTED = re.compile(r"(^|/)(conftest|__init__)\.py$")
PY_DEFAULT = re.compile(r"(^|/)(test[^/]*|[^/]+_test)\.py$")
WIDENS = re.compile(r"^\s*(?:python_files|testMatch|testRegex)\b", re.M)
JS_TEST_NAME = re.compile(r"(^|/)(test-[^/]+|[^/]+[-_]test)\.[cm]?[jt]sx?$")
CODE = re.compile(r"\.(py|[cm]?[jt]sx?|go|rs|rb|java|kt|cs)$")
NAMES = (
    (r"\.py$", r"^[ \t]*(?:async[ \t]+)?def[ \t]+(test\w*)[ \t]*\("),
    # a test call names its test and then hands over a function or options: test('name', ...). A group
    # title is no test, and neither is pattern.test('text').
    (r"\.[cm]?[jt]sx?$", r"\b(?:test|it)(?!\.describe\b)(?:\.\w+)*\s*\(\s*(?:'([^']+)'|\"([^\"]+)\"|`([^`]+)`)\s*,"),
    (r"\.go$", r"^func[ \t]+(Test\w+)[ \t]*\("),
    (r"\.rs$", r"#\[(?:\w+::)?test\][\s\S]{0,80}?fn[ \t]+(\w+)"),
    (r"\.(?:java|kt|cs)$", r"@Test\b[\s\S]{0,200}?(?:void|fun)[ \t]+(\w+)[ \t]*\("),
    (r"\.rb$", r"^[ \t]*def[ \t]+(test_\w+)|^[ \t]*it[ \t]+['\"]([^'\"]+)['\"]"),
)
FIXTURE = re.compile(r"^[ \t]*@(?:[\w.]*\.)?fixture\b(?:\((?:[^()]|\([^()]*\))*\))?[ \t]*\n"
                     r"(?:[ \t]*(?:@[^\n]*|#[^\n]*)?\n)*[ \t]*(?:async[ \t]+)?def[ \t]+(\w+)", re.M)
ASSERTION = re.compile(r"^\s*assert\b|\bexpect\s*\(|\bassert[A-Z_]\w*\s*\(|\.should\b|\bt\.(Error|Fatal)f?\(|\bassert!\(|\bassert_eq!\("
                       r"|\bpytest\.raises\(|\b(?:require|assert)\.[A-Z]\w*\(|\bAssert\.\w+\(", re.M)
# The summary each test runner prints. One runner is read per run: the one whose summary comes
# last in the output. The same words anywhere else say nothing about the tests. A duration may be
# below nothing: the runner prints one when the clock of the machine steps back while the tests run.
PYTEST_WORDS = r"(?:passed|failed|skipped|deselected|xfailed|xpassed|warnings?|errors?|rerun)"
RUNNERS = (
    ("pytest", rf"^=*\s*(?:(?:\d+ {PYTEST_WORDS}(?:, )?)+|no tests ran) in -?[\d.]+ ?s\b"),
    ("unittest", r"^Ran \d+ tests? in -?[\d.]+s\s*$"),       # a lone OK is no summary: anything may print it
    ("tap", r"^(?:#|\u2139) (?:tests|pass|fail|skipped|todo|cancelled) \d+\s*$"),
    ("jest", r"^Tests:\s.*\btotal\b"),
    ("cargo", r"^test result: .*\bpassed\b"),
    ("mocha", r"^\s*\d+ (?:passing|failing|pending)\b"),
    ("go", r"^\s*--- (?:PASS|FAIL|SKIP)\b"),
)
# What is printed when the tests could not be loaded, because the code they ask for is not there. Such a
# run fails whatever the tests assert, so it does not show that they can fail. The order is the order of
# preference for the line that is quoted: first the lines that name what was missing, last the runner
# saying that it could not collect.
# A test may also ask for the code while it runs, and then it is the test that fails: on a name, on
# an attribute of a module or of a class, on an argument the function does not take, on a program or a
# file that is not there. Such a test did not get as far as asking what the code does either. `None`
# has no attributes because of what code did.
ABSENT = (r"(?:ModuleNotFoundError|ImportError|NameError|FileNotFoundError)\b"
          r"|AttributeError: (?:partially initialized )?(?:module|type object) '[^']*' has no attribute\b"
          r"|AttributeError: '(?!NoneType')[^']*' object has no attribute\b"
          r"|TypeError: .*(?:\bunexpected keyword argument\b|\bpositional arguments? but\b|\bmissing \d+ required\b)")
COLLECTING = re.compile(r"\bERROR collecting\b")
LOAD_ERROR = tuple(re.compile(pattern) for pattern in (
    r"\bModuleNotFoundError: \S", r"\bcannot import name\b", r"\bImportError: \S", r"\bCannot find module\b",
    r"\bcannot find (?:package|module providing package)\b", r"\.go:\d+:\d+: undefined: ", r"\bcannot find symbol\b",
    r"\bERR_MODULE_NOT_FOUND\b", r"\bAttributeError: .*\bhas no attribute\b", r"\bNameError: name\b",
    r"\bTypeError: .*(?:\bunexpected keyword argument\b|\bpositional arguments? but\b|\bmissing \d+ required\b)",
    r"\b(?:ModuleNotFoundError|ImportError)\b", COLLECTING.pattern, r"\berrors? during collection\b"))
EMPTIED = "revision with every function emptied"
# What is given a file to run, or goes before the program it starts.
INTERPRETER = re.compile(r"(?:^|/)(?:python[\d.]*|pypy[\d.]*|(?:ba|da|z|k)?sh|env|exec|time|nohup|command)$")
# Tests may wait for an answer that emptied code never gives. The run on that tree gets ten times the
# seconds the posted revision took, and no less than this many; running out of time is one way of failing.
PATIENCE = 60
TEXT = {"encoding": "utf-8", "errors": "replace"}     # a stray byte in output is not a verdict


class CannotRun(Exception):
    pass


class Refused(Exception):
    """What was given cannot be used: a base the ledger does not allow, or a ledger that cannot be read."""


def git(cwd, *args, check=True) -> str:
    try:
        out = subprocess.run(["git", "-c", "core.quotepath=off", "-C", str(cwd), *args], capture_output=True,
                             timeout=120, **TEXT)
    except FileNotFoundError as exc:
        raise CannotRun("git is not installed or not on the path") from exc
    if check and out.returncode:
        raise CannotRun(f"git {' '.join(args)} failed: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout


def is_test(path: str) -> bool:
    return bool(TEST_PATH.search(path))


def show(repo, revision: str, path: str) -> str | None:
    if not revision:
        return None
    out = subprocess.run(["git", "-C", str(repo), "show", f"{revision}:{path}"], capture_output=True, **TEXT)
    return out.stdout if out.returncode == 0 else None


def raw(repo, revision: str, path: str) -> bytes | None:
    """A file as its bytes, which is what the parser asks for: a Python file may name its own encoding."""
    if not revision:
        return None
    out = subprocess.run(["git", "-C", str(repo), "show", f"{revision}:{path}"], capture_output=True)
    return out.stdout if out.returncode == 0 else None


def blob(repo, revision: str, path: str) -> str | None:
    """The name git gives the content of a file. Two files of one name hold the same bytes."""
    if not revision:
        return None
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{revision}:{path}"],
                         capture_output=True, **TEXT)
    return out.stdout.strip() or None if out.returncode == 0 else None


def commit_of(repo, name: str) -> str:
    """The full revision a name stands for, or nothing when the repository holds no such commit."""
    out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{name}^{{commit}}"],
                         capture_output=True, **TEXT)
    return out.stdout.strip() if out.returncode == 0 else ""


def runner_of(text: str) -> str | None:
    """The runner whose summary comes last in the output, or None."""
    runner = None
    for line in text.splitlines():
        for name, summary in RUNNERS:
            if re.match(summary, line):
                runner = name
    return runner


def counts(text: str) -> tuple[int | None, int]:
    """(tests that ran, tests left out) as the runner's own summaries say, all of them added up: a
    command may run its runner more than once. The first is None when no summary this tool knows
    was printed, and then nothing is concluded from it."""
    lines = text.splitlines()
    runner = runner_of(text)
    if runner is None:
        return None, 0
    mine = [n for n, line in enumerate(lines) if re.match(dict(RUNNERS)[runner], line)]

    def total(pattern: str, among: list) -> int:
        return sum(int(n) for line in among for n in re.findall(pattern, line))

    own = [lines[n] for n in mine]
    if runner == "pytest":
        return total(r"(\d+) (?:passed|failed)\b", own), total(r"(\d+) (?:skipped|deselected|xfailed|xpassed)\b", own)
    if runner == "unittest":
        ran = total(r"^Ran (\d+) tests?", own)
        verdicts = [next((l for l in lines[n + 1:n + 4] if re.match(r"^(?:OK|FAILED) \(", l)), "") for n in mine]
        left = total(r"(?:skipped|expected failures)=(\d+)", verdicts)     # each verdict follows within three lines
        return ran - left, left                 # unittest counts them among the tests it ran
    if runner == "tap":
        return total(r" (?:pass|fail) (\d+)", own), total(r" (?:skipped|todo) (\d+)", own)
    if runner == "jest":
        return total(r"(\d+) (?:passed|failed)\b", own), total(r"(\d+) (?:skipped|todo|pending)\b", own)
    if runner == "cargo":
        return total(r"(\d+) (?:passed|failed)\b", own), total(r"(\d+) ignored\b", own)
    if runner == "mocha":
        return total(r"(\d+) (?:passing|failing)\b", own), total(r"(\d+) pending\b", own)
    return (len([line for line in own if re.search(r"--- (?:PASS|FAIL)\b", line)]),
            len([line for line in own if re.search(r"--- SKIP\b", line)]))


def absent_in_pytest(text: str) -> int:
    """The failed tests that pytest puts down to code that is not there. Read are the errors under its
    heading FAILURES, one per test, where the whole message stands; the short lines of the summary are
    cut to the width of the screen and are read where there is nothing else."""
    told = 0
    for block in re.split(r"^=+ FAILURES =+$", text, flags=re.M)[1:]:
        block = re.split(r"^=+ .+ =+$", block, maxsplit=1, flags=re.M)[0]
        for test in re.split(r"^_{2,} .+ _{2,}$", block, flags=re.M)[1:]:
            error = re.search(r"^E\s+(\S.*)$", test, re.M)
            told += bool(error and re.match(ABSENT, error.group(1)))
    return max(told, len(re.findall(rf"^FAILED (?:(?! - ).)+ - (?:{ABSENT})", text, re.M)))


def failed(text: str) -> int:
    """The tests that failed while they ran, as the runner's own summaries say. A test that the output
    itself puts down to code that is missing is not among them. Without a summary none is known."""
    lines = text.splitlines()
    runner = runner_of(text)
    if runner is None:
        return 0
    mine = [n for n, line in enumerate(lines) if re.match(dict(RUNNERS)[runner], line)]
    own = [lines[n] for n in mine]

    def total(pattern: str, among: list) -> int:
        return sum(int(n) for line in among for n in re.findall(pattern, line))

    if runner == "pytest":
        return max(0, total(r"(\d+) failed\b", own) - absent_in_pytest(text))
    if runner == "unittest":                    # a test that could not import what it asks for is one of its errors
        verdicts = [next((l for l in lines[n + 1:n + 4] if re.match(r"^(?:OK|FAILED) \(", l)), "") for n in mine]
        missing = len([block for block in re.split(r"^={70}\n", text, flags=re.M) if block.startswith("ERROR: ")
                       and re.search(rf"^(?:{ABSENT})", block, re.M)])
        return max(0, total(r"(?:failures|errors)=(\d+)", verdicts) - missing)
    if runner == "tap":                         # node counts a file that could not be loaded as a failed test
        missing = len(re.findall(r"^#?\s{2,}code: '(?:ERR_)?MODULE_NOT_FOUND',?\s*$", text, re.M))   # as node prints an error
        return max(0, total(r" fail (\d+)", own) - missing)
    if runner == "mocha":
        return total(r"(\d+) failing\b", own)
    if runner == "go":
        return len([line for line in own if re.search(r"--- FAIL\b", line)])
    return total(r"(\d+) failed\b", own)       # jest and cargo


def could_not_load(text: str, code: int) -> str:
    """The line that says which code the tests did not find, when a run failed and no test failed while
    it ran. Empty when the run passed, when a test did fail, or when nothing says that code was missing.
    The folder a file was looked for in is left out: it is a temporary copy, gone when the card is read."""
    if code == 0 or failed(text):
        return ""
    lines = [line.strip() for line in text.splitlines()]
    for pattern in LOAD_ERROR:
        for n, line in enumerate(lines):
            if pattern.search(line):
                if COLLECTING.search(line):     # the heading of the runner: the error stands below it
                    below = next((low for low in lines[n + 1:n + 40] if re.match(r"E\s+\S", low)), "")
                    line = line.strip("_= ") + (": " + re.sub(r"^E\s+", "", below) if below else "")
                return re.sub(r" \((?:/|[A-Za-z]:\\)[^)]*\)?", "", re.sub(r"^(?:E|#)\s+", "", line))[:160]
    return ""


def parsed(text):
    """A Python file as the parser reads it, given as text or as bytes. What the parser has to say about
    the style of the file is not printed."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(text)


def functions(text) -> dict | None:
    """Every function a Python file defines, by its dotted name, as the parser reads it: comments and
    blank lines are no part of that. None when the file cannot be read as Python."""
    found = {}

    def walk(node, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(prefix + child.name, []).append(ast.dump(child))
            named = isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            walk(child, prefix + child.name + "." if named else prefix)

    try:
        walk(parsed(text), "")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    return found


def kept(text) -> dict | None:
    """The functions a Python file holds once it has run, by their dotted names. A name that is defined
    again, given another value or deleted replaces what it was, and a class that is defined again takes
    its functions with it. What stands under a condition counts as if it ran. A function is not looked
    into. None when the file cannot be read as Python."""
    found = {}

    def drop(name: str):
        for dotted in [dotted for dotted in found if dotted == name or dotted.startswith(name + ".")]:
            del found[dotted]

    def walk(body: list, prefix: str):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                drop(prefix + node.name)
                found[prefix + node.name] = ast.dump(node)
            elif isinstance(node, ast.ClassDef):
                drop(prefix + node.name)
                walk(node.body, prefix + node.name + ".")
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
                targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
                for target in targets:
                    for name in ast.walk(target):
                        if isinstance(name, ast.Name):
                            drop(prefix + name.id)
            else:
                for field in ("body", "orelse", "handlers", "finalbody", "cases"):
                    inner = getattr(node, field, None)
                    if isinstance(inner, list):
                        walk(inner, prefix)

    try:
        walk(parsed(text).body, "")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    return found


def reads_the_same(path: str, old: str, new: str) -> bool:
    """Whether two Python files are one program to the parser: they differ in comments and layout only."""
    try:
        return path.endswith(".py") and ast.dump(parsed(old)) == ast.dump(parsed(new))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return False


def weakened(path: str, old: str, new: str, wide: bool) -> list:
    """What a test file lost against the one that was accepted, in words. Tests that were added are no
    loss. A decorator belongs to its test, so a table of cases that lost a row is a changed test."""
    declared = names_in(path, old, wide)
    gone = declared - names_in(path, new, wide)
    lost = [f"{', '.join(sorted(gone))} is gone, removed or renamed"] if gone else []
    if path.endswith(".py"):
        then, now = functions(old), functions(new)
        if then is None or now is None:
            lost.append("the file cannot be read as Python, so it was compared as a whole, and it is not the one that was accepted")
        else:
            def bodies(found: dict, name: str):
                return collections.Counter(body for dotted, all_of in found.items()
                                           if dotted.rpartition(".")[2] == name for body in all_of)
            def running(found: dict, name: str):
                return collections.Counter(body for dotted, body in found.items() if dotted.rpartition(".")[2] == name)
            changed = sorted(name for name in declared - gone if bodies(then, name) - bodies(now, name))
            if changed:
                lost.append(f"the body of {', '.join(changed)} is not the one that was accepted")
            ran, runs = kept(old) or {}, kept(new) or {}
            again = sorted(name for name in declared - gone - set(changed) if running(ran, name) - running(runs, name))
            if again:       # the accepted test is still written in the file, and another one runs under its name
                lost.append(f"{', '.join(again)} is defined again, or its class or its name is, below the test that was "
                            f"accepted: what runs is not that test")
    was, has = len(ASSERTION.findall(old)), len(ASSERTION.findall(new))
    if has < was:
        lost.append(f"{has} assertion(s) where {was} were accepted")
    was, has = len(SKIP.findall(old)), len(SKIP.findall(new))
    if has > was:
        lost.append(f"{has} skip marker(s) where {was} were accepted")
    return lost


def outside(text) -> tuple:
    """What a Python file holds beside what it imports: its functions by their dotted names, and every
    other statement of the file and of its classes, each with the words it is written in. A function is
    not looked into: what it holds is part of it."""
    found, said = {}, []

    def walk(body: list, prefix: str):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found.setdefault(prefix + node.name, []).append(ast.dump(node))
            elif isinstance(node, ast.ClassDef):
                head = [ast.dump(part) for part in node.decorator_list + node.bases + node.keywords]
                said.append((f"class {prefix}{node.name} {head}", f"class {prefix}{node.name}"))
                walk(node.body, prefix + node.name + ".")
            elif not isinstance(node, (ast.Import, ast.ImportFrom)) and not (
                    isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)):
                said.append((ast.dump(node), " ".join(ast.unparse(node).split())[:80]))

    walk(parsed(text).body, "")
    return found, said


def beside(path: str, old: str, new: str, wide: bool = False) -> str:
    """What a test file changed beside its tests, in words for a note; empty when it changed nothing.
    The tests themselves are compared by weakened(). A helper, a fixture or a table of cases that is not
    the one that was accepted may change what a test asks, and may be what honest work looks like: this
    tool cannot tell, so it is said and no more. What a file imports and what was added are left out."""
    if not path.endswith(".py"):
        return "" if old == new else ("its tests are all declared still and its assertions and skip markers were counted; "
                                      "the body of a test is read in Python files only, so what its tests do was not compared")
    try:
        (then, said_then), (now, said_now) = outside(old), outside(new)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return ""                               # weakened() says that the file cannot be read
    declared = names_in(path, old, wide)
    changed = [f"the function {name}" for name in sorted(then) if name.rpartition(".")[2] not in declared
               and collections.Counter(then[name]) - collections.Counter(now.get(name, []))]
    left = collections.Counter(dump for dump, _ in said_now)
    for dump, words in said_then:
        if left[dump]:
            left[dump] -= 1
        else:
            changed.append(f"the head of {words}" if words.startswith("class ") else f"the line `{words}`")
    if not changed:
        return ""
    more = f" and {len(changed) - 6} more" if len(changed) > 6 else ""
    return (f"its tests are as they were accepted, and beside them this changed or is gone: {'; '.join(changed[:6])}{more}. "
            f"What that does to the tests was not compared")


def hollow(text, was) -> tuple:
    """A Python file in which every function that the work changed or added returns nothing: the
    signature, the decorators and a docstring stay, the body is `return None`. What stands outside a
    function stays as it is, and so does a function that is as it was. A first line that names the
    program to run the file with stays too: without it a script that the tests start would not start,
    and the run would fail for a reason that says nothing about the tests. `was` is the file before the
    work, None when there was none; both are text or bytes. Returns the new text and the dotted names
    of what was emptied, or (None, []) when the file cannot be read as Python or holds nothing to empty."""
    known = (functions(was) if was is not None else None) or {}
    emptied = []
    first = (text.decode("utf-8", "replace") if isinstance(text, bytes) else text).partition("\n")[0].rstrip("\r")

    def walk(node, prefix: str):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if ast.dump(child) in known.get(prefix + child.name, []):
                    continue                    # as it was before the work
                body = child.body[:1] if ast.get_docstring(child, clean=False) is not None else []
                body.append(ast.Return(value=ast.Constant(value=None)))
                if [ast.dump(part) for part in body] != [ast.dump(part) for part in child.body]:
                    child.body = body
                    emptied.append(prefix + child.name)
            else:
                walk(child, prefix + child.name + "." if isinstance(child, ast.ClassDef) else prefix)

    try:
        tree = parsed(text)
        walk(tree, "")
        if not emptied:
            return None, []
        return (first + "\n" if first.startswith("#!") else "") + ast.unparse(ast.fix_missing_locations(tree)) + "\n", emptied
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None, []


def run_by(cmd: str, path: str, subdir: str) -> bool:
    """Whether the command itself runs a file: as the program, as what an interpreter is given, or as a
    module after -m. Such a file runs the tests: emptied, it would run none of them and pass. A file
    that the command names anywhere else, as the value of an option or in a comment, is not run by it."""
    rel = relative(path, subdir)
    module = re.sub(r"\.py$", "", rel).replace("/", ".")
    for part in re.split(r"[;&|()\n]+", cmd):
        program = True                          # the next word that is no option is what runs
        words = [word.strip("\"'") for word in part.split()]
        for n, word in enumerate(words):
            if n and words[n - 1] == "-m":
                if word in (module, re.sub(r"\.__main__$", "", module)):
                    return True
                break
            if word.startswith(("-", "#", "<", ">")) or (program and re.fullmatch(r"\w+=.*", word)):
                if word.startswith("#"):
                    break
                continue
            if not program:
                break
            word = re.sub(r"^\./", "", word)
            if word in (rel, path, rel.rpartition("/")[2]) or word.endswith("/" + rel):
                return True
            program = bool(INTERPRETER.search(word))
    return False


def read_ledger(path) -> list:
    """The verdicts written down so far, oldest first. A ledger that is not there yet holds none. One
    that cannot be read is refused whole: a line that is passed over may be the ACCEPT that names the base."""
    ledger = pathlib.Path(path)
    if not ledger.exists():
        return []
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise Refused(f"the ledger {ledger} cannot be read: {exc}") from exc
    entries = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise Refused(f"line {n} of the ledger {ledger} is not JSON") from exc
        stage = entry.get("stage") if isinstance(entry, dict) else None
        if isinstance(stage, bool) or not isinstance(stage, int) or entry.get("verdict") not in cards.VERDICTS \
                or not re.fullmatch(r"[0-9a-f]{40,64}", str(entry.get("revision"))):
            raise Refused(f"line {n} of the ledger {ledger} lacks a stage, a full revision or a verdict")
        entries.append(entry)
    return entries


def built_on(source, older: str, newer: str) -> bool:
    """Whether a revision is among the ancestors of another, or is that revision."""
    return subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor", older, newer],
                          capture_output=True).returncode == 0


def newest(source, accepted: list) -> dict:
    """The accepted revision that the work goes on from: the one written last, unless another accepted
    revision is built on it. Two checks that end together write their lines in either order."""
    last = accepted[-1]
    for entry in accepted:
        if entry["revision"] != last["revision"] and built_on(source, last["revision"], entry["revision"]):
            last = entry
    return last


def writable(path) -> None:
    """Says before anything runs that the ledger could not take the verdict. Nothing is created."""
    ledger = pathlib.Path(path)
    where = ledger
    while not where.exists() and where != where.parent:
        where = where.parent
    if where == ledger:
        fine, why = ledger.is_file() and os.access(ledger, os.W_OK), "it may not be written to"
    else:
        fine, why = where.is_dir() and os.access(where, os.W_OK | os.X_OK), (
            f"{where} may not be written to" if where.is_dir() else f"{where} is no folder")
    if not fine:
        raise CannotRun(f"the verdict could not be written into the ledger {ledger}: {why}. Nothing was run")


def folder_of(subdir: str, stage: int) -> str | None:
    """The folder of another stage: the stage folder with that stage's number in place of its own. None
    when the stage folder ends in no number."""
    number = re.search(r"\d+$", subdir)
    if not number:
        return None
    width = len(number.group()) if number.group().startswith("0") else 0
    return subdir[:number.start()] + str(stage).zfill(width)


def twin_of(path: str, subdir: str) -> str | None:
    """Where a file of another stage folder stands in this one. None when the file is in no stage folder,
    or the stage folder ends in no number."""
    number = re.search(r"\d+$", subdir)
    there = re.match(rf"{re.escape(subdir[:number.start()])}\d+/(.+)$", path) if number else None
    return f"{subdir}/{there.group(1)}" if there else None


def frozen_from(entries: list, stage: int, subdir: str, source=None) -> tuple:
    """The revision whose tests are frozen for a stage and the folder that holds them, as the ledger has
    it: the revision accepted last for the highest earlier stage, and the stage folder with that stage's
    number in place of its own. ("", None) when no earlier stage was accepted. The folder is None when
    the stage folder ends in no number: the tests are then looked for in the same folder. With the
    repository given, the revision accepted last is the newest one and not the last line."""
    earlier = [entry for entry in entries if entry["verdict"] == "ACCEPT" and entry["stage"] < stage]
    if not earlier:
        return "", None
    last = max(entry["stage"] for entry in earlier)
    of_stage = [entry for entry in earlier if entry["stage"] == last]
    revision = (newest(source, of_stage) if source else of_stage[-1])["revision"]
    return revision, folder_of(subdir, last)


def settle(args) -> list:
    """What the ledger settles before anything runs, so that no seat has to remember it: the base is the
    last accepted revision, and the frozen tests are those of the last stage accepted before this one.
    A base or frozen tests that are given and are not these are refused. A revision that the ledger holds
    as accepted for another stage is judged for this one against the base it was judged against there.
    Afterwards args holds the base and the frozen tests; what was taken from the ledger is returned in
    words, for the verdict."""
    if not args.ledger:
        return []
    entries = read_ledger(args.ledger)
    writable(args.ledger)
    source = repository(args.repo)
    posted = commit_of(source, args.revision)
    accepted = [entry for entry in entries if entry["verdict"] == "ACCEPT"]
    taken = []
    if accepted:
        last = newest(source, accepted)
        if not commit_of(source, last["revision"]):
            raise CannotRun(f"the ledger {args.ledger} holds {last['revision']} as the last accepted revision, and "
                            f"{source} holds no such revision")
        if last is not accepted[-1]:
            taken.append(f"the last accepted revision is {last['revision']} (stage {last['stage']}): the ledger "
                         f"{args.ledger} holds {accepted[-1]['revision']} in its last accepted line, and "
                         f"{last['revision']} is built on it")
        base, stands = last["revision"], (f"is not the last accepted revision. The ledger {args.ledger} holds "
                                          f"{last['revision']} as the last revision that was accepted (stage "
                                          f"{last['stage']}), and that is the base")
        held = f"the ledger {args.ledger} holds it as the last accepted revision (stage {last['stage']})"
        if posted == last["revision"]:
            mine = [entry for entry in accepted if entry["revision"] == posted]
            if any(entry["stage"] == args.stage for entry in mine):
                raise CannotRun(f"revision {posted} is the one that the ledger {args.ledger} holds as the last "
                                f"accepted revision (stage {args.stage}): nothing was delivered since, so there is "
                                f"nothing to check")
            base = str(mine[0].get("base") or "")
            if base != "empty" and commit_of(source, base) != base:
                raise Refused(f"the ledger {args.ledger} holds the posted revision {posted} as accepted for stage "
                              f"{mine[0]['stage']}, and names no base that it was judged against there")
            args.judged = sorted({entry["stage"] for entry in mine})
            held = (f"the ledger {args.ledger} holds the posted revision as accepted for stage {mine[0]['stage']} "
                    f"against this base, and stage {args.stage} of the same revision is judged against the same")
            stands = (f"is not the base the posted revision was judged against. The ledger {args.ledger} holds the "
                      f"posted revision as accepted for stage {mine[0]['stage']} against the base {base}, and that is "
                      f"the base for stage {args.stage} of the same revision")
        if not args.base:
            args.base = base
            taken.append(f"the base is {base}: {held}")
        elif ("empty" if args.base == "empty" else commit_of(source, args.base)) != base:
            raise Refused(f"--base {args.base} {stands}: a rejected or an earlier revision as the base would forgive "
                          f"what came after it. Leave --base out, or give {base}")
        if base != "empty" and posted and not built_on(source, base, posted):
            raise CannotRun(f"the posted revision {posted} is not built on {base}, which the ledger {args.ledger} holds "
                            f"as the last accepted revision (stage {last['stage']}): the accepted commit was written "
                            f"again, or the work started somewhere else, and nothing can be compared across that. Post "
                            f"a revision that is built on {base}. The ledger is kept as it is")
    elif args.base and args.base != "empty":
        given = commit_of(source, args.base)
        for entry in entries:
            if given and commit_of(source, entry["revision"]) and built_on(source, entry["revision"], given):
                how = "stands" if entry["revision"] == given else f"is built on {entry['revision']}, which stands"
                raise Refused(f"--base {given} {how} in the ledger {args.ledger} as rejected, and the ledger holds no "
                              f"accepted revision: a rejected revision is never a base, and neither is one that is built "
                              f"on it, because what was rejected would be forgiven. Give the revision the work started "
                              f"from, or `empty` when nothing came before it")
    revision, folder = frozen_from(entries, args.stage, (args.subdir or "").strip("/"), source)
    if revision:
        stage = max(entry["stage"] for entry in accepted if entry["stage"] < args.stage)
        if args.frozen_ref and commit_of(source, args.frozen_ref) != revision:
            raise Refused(f"--frozen-ref {args.frozen_ref} is not the revision whose tests are frozen. The ledger "
                          f"{args.ledger} holds {revision} as the last revision accepted for stage {stage}, and the "
                          f"tests it was accepted with are the frozen ones. Leave --frozen-ref out, or give {revision}")
        args.frozen_ref, args.by_ledger = revision, True
        if args.frozen_dir is None and folder is not None:
            args.frozen_dir = folder
        where = args.frozen_dir if args.frozen_dir is not None else (args.subdir or "the repository")
        taken.append(f"the frozen tests are those of stage {stage}, read in {where} at {revision}: the ledger "
                     f"{args.ledger} holds it as the last revision accepted for that stage")
    elif args.frozen_ref:
        taken.append(f"the frozen tests were named by hand (--frozen-ref {args.frozen_ref}): the ledger {args.ledger} "
                     f"holds no accepted revision of an earlier stage")
    return taken


def record(path, verdict: dict) -> None:
    """One more line in the ledger: what was judged, against which base, and how it ended."""
    ledger = pathlib.Path(path)
    line = json.dumps({key: verdict[key] for key in ("stage", "revision", "base", "verdict", "at")})
    try:
        ledger.parent.mkdir(parents=True, exist_ok=True)
        left_open = ledger.exists() and ledger.read_bytes()[-1:] not in (b"", b"\n")
        with ledger.open("a", encoding="utf-8") as out:
            out.write(("\n" if left_open else "") + line + "\n")
    except OSError as exc:
        raise CannotRun(f"the verdict could not be written into the ledger {ledger}: {exc}") from exc


def names_in(path: str, text: str, wide: bool = False) -> set:
    """The tests a file declares. Empty for a file that is no test file; helpers and fixtures are left out.
    `wide` says that the project collects more than the usual names."""
    found = set()
    if NOT_COLLECTED.search(path):
        return found
    if path.endswith(".py"):
        if not (PY_DEFAULT.search(path) or (is_test(path) and (wide or re.search(r"\bTestCase\b", text)))):
            return found
    elif not (is_test(path) or JS_TEST_NAME.search(path)):
        return found
    for kind, pattern in NAMES:
        if re.search(kind, path):
            for match in re.findall(pattern, text, re.M):
                found.update(part for part in ([match] if isinstance(match, str) else match) if part)
    if path.endswith(".py"):
        found -= set(FIXTURE.findall(text))
        for name in list(found):
            called = re.search(rf"(?<!def )(?<![\w.]){re.escape(name)}[ \t]*\(", text)
            handed = re.search(rf"^[ \t]*(?:async[ \t]+)?def[ \t]+\w+[ \t]*\([^)]*\b{re.escape(name)}\b", text, re.M)
            if called or handed:
                found.discard(name)
    return found


def collects_widely(source, revisions: list, subdir: str, cmd: str) -> bool:
    """Whether the project or the command tells the runner to collect files under other names."""
    if re.search(r"\bdiscover\b.*\s(?:-p|--pattern)\b", cmd):
        return True
    folders = ["", subdir + "/"] if subdir else [""]
    for revision in filter(None, revisions):
        for folder in folders:
            for name in ("pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml", "package.json", "jest.config.js"):
                if WIDENS.search(show(source, revision, folder + name) or "") \
                        or re.search(r'"(?:testMatch|testRegex)"', show(source, revision, folder + name) or ""):
                    return True
    return False


def pytest_session(text: str) -> str:
    """A pytest log up to the end of the runner's last closing line. What a test prints at interpreter
    shutdown stands after it, so it is not read: a summary forged there would pass for the runner's."""
    last = None
    for found in re.finditer(dict(RUNNERS)["pytest"], text, re.M):
        last = found
    if last is None:
        return text
    end = text.find("\n", last.end())
    return text if end < 0 else text[:end + 1]


def bare_name(told: str) -> str:
    """A test's name alone, however the runner writes it: without the path, the class or the case."""
    bare = re.sub(r"\[.*\]$", "", told.split("::")[-1].strip())
    return bare.rpartition(".")[2]


def named_failures(text: str) -> list:
    """Every failed test with where the runner says it stands: (name, failed while it ran, place). The
    place is the file for pytest, the dotted id for unittest, and empty for node and go, which name the
    test alone. For pytest only the runner's own summary section is read, because a test's printed
    output stands above it: a failure block a test makes up may take a vouching away, by putting a
    failure down to absent code, but it can never grant one."""
    runner = runner_of(text)
    out = []
    if runner == "pytest":
        text = pytest_session(text)
        doubted = {name for name, ran in failures_in(text).items() if not ran}
        summary = re.split(r"^=+ short test summary info =+$", text, flags=re.M)[-1]
        for word, told, reason in re.findall(r"^(FAILED|ERROR)\s+(\S+::\S+?)(?:\s+-\s+(.*))?$", summary, re.M):
            name = bare_name(told)
            ran = word == "FAILED" and not re.match(ABSENT, reason or "") and name not in doubted
            out.append((name, ran, told.split("::")[0]))
    elif runner == "unittest":
        for block in re.split(r"^={70}\n", text, flags=re.M):
            told = re.match(r"(FAIL|ERROR): (\S+)(?: \(([^)]+)\))?", block)
            if told:
                ran = told.group(1) == "FAIL" or not re.search(rf"^(?:{ABSENT})", block, re.M)
                out.append((bare_name(told.group(2)), ran, told.group(3) or ""))
    elif runner == "tap":
        spots = list(re.finditer(r"^not ok \d+ -? ?(.*?)(?:\s+#.*)?$", text, re.M))
        for n, spot in enumerate(spots):
            detail = text[spot.end():spots[n + 1].start() if n + 1 < len(spots) else len(text)]
            out.append((spot.group(1).strip(), not re.search(r"code: '(?:ERR_)?MODULE_NOT_FOUND'", detail), ""))
    elif runner == "go":
        out += [(name, True, "") for name in re.findall(r"^\s*--- FAIL: (\S+)", text, re.M)]
    return out


def failures_in(text: str) -> dict:
    """The tests a run failed, by bare name: True when the test failed while it ran, False when the
    output itself puts the failure down to code that is not there. Read the way failed() counts, but
    name by name: pytest and unittest from their failure blocks and summary lines, node from its TAP
    entries, go from its result lines. A name is taken from where a test stands, never from a path, so
    a test is not mistaken for the file of another. A runner this tool knows less well yields nothing,
    and nothing is concluded from nothing."""
    found = {}
    runner = runner_of(text)
    name_of = bare_name

    def keep(name: str, ran: bool) -> None:
        if name:
            found[name] = found.get(name, False) or ran

    if runner == "pytest":
        text = pytest_session(text)
        for section in re.split(r"^=+ FAILURES =+$", text, flags=re.M)[1:]:
            section = re.split(r"^=+ .+ =+$", section, maxsplit=1, flags=re.M)[0]
            parts = re.split(r"^_{2,}\s*(.+?)\s*_{2,}$", section, flags=re.M)
            for header, body in zip(parts[1::2], parts[2::2]):
                error = re.search(r"^E\s+(\S.*)$", body, re.M)
                keep(name_of(header), not (error and re.match(ABSENT, error.group(1))))
        # The short lines are read from the summary section only: what a test prints is shown above it,
        # so a line a test makes up does not pass for the runner's.
        summary = re.split(r"^=+ short test summary info =+$", text, flags=re.M)[-1]
        for told, reason in re.findall(r"^FAILED\s+(\S+::\S+?)(?:\s+-\s+(.*))?$", summary, re.M):
            if name_of(told) not in found:
                keep(name_of(told), not re.match(ABSENT, reason or ""))
        for told, _ in re.findall(r"^ERROR\s+(\S+::\S+?)(?:\s+-\s+(.*))?$", summary, re.M):
            if name_of(told) not in found:
                keep(name_of(told), False)
    elif runner == "unittest":
        for block in re.split(r"^={70}\n", text, flags=re.M):
            told = re.match(r"(FAIL|ERROR): (\S+)", block)
            if told:
                keep(name_of(told.group(2)), told.group(1) == "FAIL" or not re.search(rf"^(?:{ABSENT})", block, re.M))
    elif runner == "tap":
        spots = list(re.finditer(r"^not ok \d+ -? ?(.*?)(?:\s+#.*)?$", text, re.M))
        for n, spot in enumerate(spots):
            detail = text[spot.end():spots[n + 1].start() if n + 1 < len(spots) else len(text)]
            keep(spot.group(1).strip(), not re.search(r"code: '(?:ERR_)?MODULE_NOT_FOUND'", detail))
    elif runner == "go":
        for name in re.findall(r"^\s*--- FAIL: (\S+)", text, re.M):
            keep(name, True)
    return found


def is_config(path: str) -> bool:
    return bool(CONFIG_PATH.search(path))


def clone_at(source, revision: str, target: pathlib.Path) -> pathlib.Path:
    git(".", "clone", "--quiet", "--no-hardlinks", str(source), str(target))
    git(target, "checkout", "--quiet", "--detach", revision)
    return target


def run_command(cmd: str, cwd: pathlib.Path, tree: str, timeout: int, logs: pathlib.Path, must_start=False) -> dict:
    """One run. Only on the posted revision is a command that cannot start an error: on the
    other trees the revision's own runner may be missing, and that is one way of failing."""
    started = time.monotonic()
    # in a process group of its own: a command that is stopped takes with it what it started
    # and with no standard input: a seat's shell may hand this tool an open pipe, and a command that
    # reads it would wait until it is stopped for time
    child = subprocess.Popen(cmd, shell=True, cwd=str(cwd), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True, **TEXT)
    try:
        said, errors = child.communicate(timeout=timeout)
        code, text, printed, stopped = child.returncode, said + errors, "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            child.kill()
        try:
            said, errors = child.communicate(timeout=10)
        except subprocess.TimeoutExpired:       # something it started left the group and holds the output
            said = errors = ""
        # what it had printed is kept for the reader; a summary of a run that did not end counts for nothing
        code, text, printed, stopped = 124, f"timed out after {timeout} s", (said or "") + (errors or ""), True
    seconds = round(time.monotonic() - started, 2)
    if must_start and (code == 127 or (code != 0 and len(text) < 300 and "command not found" in text.lower())):
        raise CannotRun(f"the test command could not be started on the {tree}: {text.strip()[:200]}")
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / (re.sub(r"[^a-z0-9]+", "-", tree.lower()).strip("-") + ".log")
    log.write_text(printed + ("\n" if printed and not printed.endswith("\n") else "") + text, encoding="utf-8")
    print(f"ran on {tree}: {cmd} (exit {code}, {seconds} s)")
    ran, left = counts(text)
    run = {"tree": tree, "cmd": cmd, "exit_code": code, "seconds": seconds, "log": str(log),
           "runner": runner_of(text), "tests_run": ran, "tests_left_out": left, "tests_failed": failed(text),
           "could_not_load": could_not_load(text, code)}
    if stopped:
        run["stopped_after"] = timeout
    return run


def inside(path: pathlib.Path, tree: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(tree.resolve())
        return True
    except ValueError:
        return False


def under(path: str, subdir: str) -> bool:
    return not subdir or path == subdir or path.startswith(subdir.rstrip("/") + "/")


def relative(path: str, subdir: str) -> str:
    return path[len(subdir.rstrip("/")) + 1:] if subdir and path.startswith(subdir.rstrip("/") + "/") else path


def repository(path) -> pathlib.Path:
    source = pathlib.Path(path).resolve()
    if not (source / ".git").exists() and not source.name.endswith(".git"):
        raise CannotRun(f"{source} is not a git repository")
    return source


def changes_into(cmd: str, subdir: str) -> tuple:
    """The folder and the rest of a command that begins by changing into the stage folder, or two
    empty words. The check runs every command inside that folder already, so such a command would
    fail on its first word, whatever the work is."""
    found = re.match(r"""\s*cd\s+(?:"([^"]+)"|'([^']+)'|(\S+?))\s*(?:&&|;)\s*(\S.*)$""", cmd, re.S) if subdir else None
    if not found:
        return "", ""
    target = re.sub(r"^\./", "", next(group for group in found.groups()[:3] if group)).rstrip("/")
    return (target, found.group(4).strip()) if target in (subdir, subdir.rpartition("/")[2]) else ("", "")


def set_aside(out: pathlib.Path) -> None:
    """Makes room for the card. What an earlier check left there must not pass for the verdict of this one,
    and is not destroyed either: a check that does not run could not write it again."""
    if out.is_dir():
        raise Refused(f"--out {out} is a folder: name the file that the verdict is written to")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            earlier = out.with_name(out.name + ".earlier")
            os.replace(out, earlier)
            print(f"the card of an earlier check that stood at {out} is now {earlier}")
    except OSError as exc:
        raise Refused(f"--out {out} cannot be written to: {exc}") from exc


def examine(args) -> tuple[dict, int]:
    source = repository(args.repo)
    if subprocess.run(["git", "-C", str(source), "cat-file", "-e", f"{args.revision}^{{commit}}"],
                      capture_output=True).returncode:
        raise CannotRun(f"revision {args.revision} is not in {source}")
    revision = git(source, "rev-parse", f"{args.revision}^{{commit}}").strip()
    if args.base == "empty":
        base, compare = "", git(source, "hash-object", "-t", "tree", "/dev/null").strip()
    else:
        if subprocess.run(["git", "-C", str(source), "cat-file", "-e", f"{args.base}^{{commit}}"],
                          capture_output=True).returncode:
            raise CannotRun(f"base {args.base} is not in {source}")
        base = compare = git(source, "rev-parse", f"{args.base}^{{commit}}").strip()
        if base == revision:
            raise CannotRun("the base and the posted revision are the same: nothing was delivered")
        if subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor", base, revision],
                          capture_output=True).returncode:
            raise CannotRun(f"base {base[:7]} is not an ancestor of revision {revision[:7]}")
    subdir = (args.subdir or "").strip("/")
    target, rest = changes_into(args.test_cmd, subdir)
    if target and subprocess.run(["git", "-C", str(source), "cat-file", "-e", f"{revision}:{subdir}/{target}"],
                                 capture_output=True).returncode:
        raise Refused(f"--test-cmd changes into the stage folder ({target}), and the check already runs every command "
                      f"inside {subdir}: the command would fail on its first word, whatever the work is. Give the "
                      f"command as it is run from inside the stage folder: {rest}")
    frozen_dir = (args.frozen_dir if args.frozen_dir is not None else subdir).strip("/")
    frozen_ref, frozen_dir = "", ("" if frozen_dir == "." else frozen_dir)
    if args.frozen_ref:
        frozen_ref = commit_of(source, args.frozen_ref)
        if not frozen_ref:
            raise CannotRun(f"the revision of the frozen tests, {args.frozen_ref}, is not in {source}")
        if frozen_ref == revision and not getattr(args, "by_ledger", False):
            raise Refused(f"--frozen-ref {args.frozen_ref} is the posted revision itself: its tests would be compared "
                          f"with themselves, and every one of them would count as accepted. The frozen tests are those "
                          f"of the revision that was accepted for the earlier stage")
    listed = {p.strip() for p in (args.changed_tests or "").split(",") if p.strip()}
    notes = dict(item.split("=", 1) for item in (args.spec_change or []) if "=" in item)
    out_path = pathlib.Path(args.out) if args.out else None
    logs = pathlib.Path(str(out_path) + ".logs") if out_path else pathlib.Path(tempfile.mkdtemp(prefix="falsify-logs-"))
    findings, runs, remarks, emptied = [], [], list(getattr(args, "taken", [])), []
    named = "--frozen-ref and --frozen-dir" if args.ledger else "--ledger, or --frozen-ref and --frozen-dir"
    unnamed = (f"the ledger {args.ledger} holds no accepted revision of an earlier stage, so no frozen tests were named"
               if args.ledger else "no frozen tests were named either")

    def earlier(path: str) -> str:
        """Where a file of the stage folder stands in the folder of the frozen tests."""
        rel = relative(path, subdir)
        return f"{frozen_dir}/{rel}" if frozen_dir else rel

    def only_copied(status: str, path: str) -> bool:
        """Whether a file holds nothing that the work wrote: it is new and empty, or it is byte for byte
        the file that was accepted with the frozen tests. Such a file was only copied forward."""
        now = blob(source, revision, path)
        if now is None:
            return False
        if status == "A" and raw(source, revision, path) == b"":
            return True
        return bool(frozen_ref) and now == blob(source, frozen_ref, earlier(path))

    work = pathlib.Path(tempfile.mkdtemp(prefix="falsify-"))
    try:
        rev_tree = clone_at(source, revision, work / "revision")
        cwd = rev_tree / subdir if subdir else rev_tree
        if not cwd.is_dir():
            raise CannotRun(f"{subdir} does not exist at revision {revision[:7]}")
        first = run_command(args.test_cmd, cwd, "revision", args.timeout, logs, must_start=True)
        runs.append(first)
        if first.get("stopped_after"):
            findings.append(f"the claimed command did not finish on the posted revision within {args.timeout} s "
                            f"(--timeout) and was stopped; log {first['log']}")
        elif first["exit_code"]:
            findings.append(f"the claimed command does not pass on the posted revision (exit {first['exit_code']}); log {first['log']}")
        before = None                           # the run on the base as it was, when there is a base
        if base:
            base_cwd = clone_at(source, base, work / "base-as-it-was")
            base_cwd = base_cwd / subdir if subdir else base_cwd
            if base_cwd.is_dir():
                before = run_command(args.test_cmd, base_cwd, "base as it was", args.timeout, logs)
                runs.append(before)
            else:
                remarks.append(f"the base holds no folder {subdir}, so there was no run on the base as it was, and the "
                               f"numbers of tests were compared with nothing" + ("" if frozen_ref else
                               f"; {unnamed}, so every file in {subdir} counts as new: when the "
                               f"folder is an earlier one copied forward, give {named}"))

        changed, beyond = [], []
        for line in git(source, "diff", "--name-status", "--no-renames", compare, revision).splitlines():
            status, _, path = line.partition("\t")
            if path:
                (changed if under(path, subdir) else beyond).append((status.strip()[:1], path))
        if beyond:
            # What the work changed outside its folder is not judged here, and the revision becomes the base
            # of what comes next: so it is named, and a test that was accepted there is not given up unseen.
            judged = {folder_of(subdir, stage): stage for stage in getattr(args, "judged", [])}
            elsewhere, twins = set(), []
            for status, path in beyond:
                if status in ("M", "D") and (is_test(path) or is_config(path)) and not notes.get(path):
                    there = [stage for folder, stage in judged.items() if folder and under(path, folder)]
                    elsewhere.update(there)
                    twin = twin_of(path, subdir)
                    if not there and twin and blob(source, revision, twin) is not None \
                            and blob(source, revision, twin) == blob(source, revision, path):
                        twins.append(path)      # mended in both folders alike, and judged here in this one
                    elif not there:
                        findings.append(f"test file outside {subdir} {'deleted' if status == 'D' else 'changed'}: {path}. "
                                        f"The work is judged in {subdir}, and a test outside it is as it was accepted, has "
                                        f"a --spec-change note, or is mended the same way in both folders. When the "
                                        f"revision mends another stage as well, have it checked for that stage first")
            beyond.sort(key=lambda item: (not (is_test(item[1]) or is_config(item[1])), item[0] == "A", item[1]))
            more = f" and {len(beyond) - 6} more" if len(beyond) > 6 else ""
            remarks.append(f"the work also changed {len(beyond)} file(s) outside {subdir}, which this check did not judge: "
                           f"{', '.join(path for _, path in beyond[:6])}{more}" + "".join(
                               f"; the ledger holds this revision as accepted for stage {stage}, where what it changed "
                               f"in {folder_of(subdir, stage)} was judged" for stage in sorted(elsewhere)) + (
                               f"; the same as the file of its name in {subdir}, which is judged here: {', '.join(twins[:6])}"
                               f"{f' and {len(twins) - 6} more' if len(twins) > 6 else ''}" if twins else ""))
        tests = [(s, p) for s, p in changed if is_test(p)]
        configs = [(s, p) for s, p in changed if is_config(p) and not is_test(p)]
        others = [(s, p) for s, p in changed if not is_test(p) and not is_config(p)]
        if not base:            # nothing before the work: a test deleted on the way is in no diff
            gone = git(source, "log", "--diff-filter=D", "--name-only", "--format=", revision).splitlines()
            for path in sorted({g for g in gone if g and under(g, subdir) and is_test(g)}):
                if show(source, revision, path) is None:
                    findings.append(f"deleted test file: {path} (in an earlier commit of this work)")

        forgiven = 0                            # tests that are gone from a file that carries a note
        wide = collects_widely(source, [base, revision], subdir, args.test_cmd)
        now_declared = set()
        for status, path in tests:
            if status != "D":
                now_declared |= names_in(path, show(source, revision, path) or "", wide)
        for status, path in tests:
            if status == "D":
                findings.append(f"deleted test file: {path}")
                continue
            if status == "M":
                gone = names_in(path, show(source, base, path) or "", wide) - now_declared
                if gone and (notes.get(path) or notes.get(relative(path, subdir))):
                    forgiven += len(gone)
                elif gone:
                    findings.append(f"a test that was there and is gone, removed or renamed, in {path}: "
                                    f"{', '.join(sorted(gone))}")
            if not CODE.search(path):
                continue                        # a fixture in prose or data holds no test
            # a file that is new in a stage folder is compared with the one it was copied from, when that is known
            new = show(source, revision, path) or ""
            old = (show(source, base, path) if status == "M" else show(source, frozen_ref, earlier(path))) or ""
            if len(SKIP.findall(new)) > len(SKIP.findall(old)):
                findings.append(f"skip marker added in {path}")
            excused = notes.get(path) or notes.get(relative(path, subdir))
            if status == "M" and len(ASSERTION.findall(new)) < len(ASSERTION.findall(old)) and not excused:
                findings.append(f"assertions removed in {path}: {len(ASSERTION.findall(old))} before, {len(ASSERTION.findall(new))} after")
        # What the runner itself reports, against the base as it was. This does not depend on how a
        # test was switched off: a marker, a rename, a configuration line all show up as a count.
        ran, left = first["tests_run"], first["tests_left_out"]
        if before and before["runner"] and first["runner"] and before["runner"] != first["runner"]:
            remarks.append(f"the two runs end with different runners ({before['runner']} on the base, {first['runner']} "
                           f"on the revision), so the numbers of tests were not compared")
        elif before and before["tests_run"] is None:
            remarks.append("the run on the base printed no summary this tool knows, so the numbers of tests were not compared"
                           + (f"; the run on the revision left out {left} test(s): look at {first['log']}" if left else ""))
        else:
            was_left = before["tests_left_out"] if before else 0
            if left > was_left:
                findings.append(f"the run on the posted revision left out {left} test(s), skipped or deselected, "
                                f"against {was_left} on the base; log {first['log']}")
            if before and ran is None:
                findings.append(f"the run on the posted revision printed no summary of its tests, and the run on the "
                                f"base did: the number of tests that ran cannot be compared; log {first['log']}")
            elif before and ran < before["tests_run"] - forgiven:
                findings.append(f"fewer tests ran on the posted revision ({ran}) than on the base "
                                f"({before['tests_run']}); log {first['log']}")
            elif ran is None:
                remarks.append("the runner printed no summary this tool knows, so the numbers of tests were not compared")
        for remark in remarks:
            print(f"note: {remark}")

        for status, path in tests + configs:
            if path not in listed and relative(path, subdir) not in listed and not only_copied(status, path):
                what = "test file" if is_test(path) else "test configuration"
                findings.append(f"{what} changed but not listed in the handoff: {path}")

        # The frozen tests are compared by what they declare, not byte for byte: a stage folder is the
        # earlier one copied and widened, so a test file that gained a test is what honest work looks like.
        if frozen_ref:
            names = git(source, "ls-tree", "-r", "--name-only", frozen_ref, *([frozen_dir] if frozen_dir else [])).splitlines()
            names = [name for name in names if is_test(name)]
            if not names:
                remarks.append(f"no test file stands in {frozen_dir or 'the repository'} at {frozen_ref}, where the frozen "
                               f"tests were looked for: no frozen test was compared. When the earlier stage stands in "
                               f"another folder, name it with --frozen-dir")
            for old_path in names:
                rel = relative(old_path, frozen_dir)
                new_path = f"{subdir}/{rel}" if subdir else rel
                if notes.get(new_path) or notes.get(rel) or blob(source, frozen_ref, old_path) == blob(source, revision, new_path):
                    continue
                old, new = show(source, frozen_ref, old_path) or "", show(source, revision, new_path)
                if new is None:
                    findings.append(f"frozen test file is gone without a spec-change note: {new_path} was accepted with "
                                    f"the earlier stage and is not in the posted revision")
                elif not CODE.search(new_path):
                    remarks.append(f"{new_path} is among the frozen tests, is not code and has changed: what it holds "
                                   f"was not compared")
                else:
                    lost = weakened(new_path, old, new, wide)
                    if lost:
                        findings.append(f"frozen test changed without a spec-change note: {new_path}: {'; '.join(lost)}")
                    elif not names_in(new_path, old, wide) and not reads_the_same(new_path, old, new):
                        remarks.append(f"{new_path} is among the frozen tests, declares no test and has changed: its "
                                       f"assertions and skip markers were counted, what it does was not compared")
                    elif names_in(new_path, old, wide) and beside(new_path, old, new, wide):
                        remarks.append(f"{new_path} is among the frozen tests and has changed: "
                                       f"{beside(new_path, old, new, wide)}")

        delivered = [p for s, p in tests if s in ("A", "M")]
        not_made = (f"so the runs that ask whether delivered tests can fail were not made: on the base with delivered "
                    f"tests, on the revision with implementation reverted, on the {EMPTIED}")
        if not tests:
            print("no test file changed: canary skipped")
            remarks.append(f"the work changed no test file, {not_made}")
        elif not delivered or not others:
            print("no implementation change beside the tests: canary skipped")
            remarks.append(("the work changed nothing beside its test files, " if delivered else
                            "the work delivered no test file, ") + not_made)
        else:
            if base:
                base_tree = clone_at(source, base, work / "base")
            else:
                base_tree = work / "base"
                base_tree.mkdir()
            for path in delivered:
                target = base_tree / path
                if (rev_tree / path).is_symlink() or not inside(target.parent, base_tree):
                    findings.append(f"test path leaves the repository: {path}")
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(rev_tree / path, target)
            pcwd = base_tree / subdir if subdir else base_tree
            pcwd.mkdir(parents=True, exist_ok=True)
            second = run_command(args.test_cmd, pcwd, "base with delivered tests", args.timeout, logs)
            runs.append(second)
            if second["exit_code"] == 0:
                findings.append("passes without the implementation: the delivered tests pass on the base")

            reverted = clone_at(source, revision, work / "reverted")
            for status, path in others:
                if status == "A":
                    (reverted / path).unlink(missing_ok=True)
                else:
                    git(reverted, "checkout", "--quiet", base, "--", path)
            third = run_command(args.test_cmd, reverted / subdir if subdir else reverted,
                                "revision with implementation reverted", args.timeout, logs)
            runs.append(third)
            if third["exit_code"] == 0:
                findings.append("passes without the implementation: the delivered tests pass with the implementation reverted")

            # When the base holds no stage folder, the two runs above fail for want of code, whatever the
            # tests say. The delivered tests then also run on the earlier stage's accepted code, where the
            # code they ask about exists as it was: a test of a changed behaviour can fail there, and that
            # is evidence. What passes there proves nothing and accuses nothing: a stage asks for more than
            # the stage before, not for something else.
            earlier_run = None
            if base and before is None and frozen_ref and frozen_dir:
                earlier_tree = clone_at(source, frozen_ref, work / "earlier-stage")
                for path in delivered:
                    target = earlier_tree / earlier(path)
                    if (rev_tree / path).is_symlink() or not inside(target.parent, earlier_tree):
                        continue                    # already a finding from the copy onto the base
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(rev_tree / path, target)
                fcwd = earlier_tree / frozen_dir
                if fcwd.is_dir():
                    earlier_run = run_command(args.test_cmd, fcwd, "earlier stage with delivered tests",
                                              args.timeout, logs)
                    runs.append(earlier_run)

            # The run that shows it when the two above could not load the tests: the code is all there, and
            # every function the work changed or added returns nothing. A file that is new in its folder is
            # compared with the one it was copied from, so that what the earlier stage wrote stays as it is.
            tried, left, added, fourth = [run for run in (second, third, earlier_run) if run], [], [], None
            written = [(s, p) for s, p in tests if s in ("A", "M") and not only_copied(s, p)]
            if written:
                hollowed = clone_at(source, revision, work / "emptied")
                for status, path in others:
                    target, text = hollowed / path, raw(source, revision, path)
                    if text is None or not path.endswith(".py") or target.is_symlink() or not inside(target.parent, hollowed):
                        continue
                    if run_by(args.test_cmd, path, subdir):
                        left.append(f"{path} is named by the listed command, so it runs the tests")
                        continue
                    was = raw(source, base, path)
                    was = was if was is not None else raw(source, frozen_ref, earlier(path))
                    new, names = hollow(text, was)
                    if new is not None:
                        target.write_text(new, encoding="utf-8")
                        emptied += [f"{path}: {name}" for name in names]
                        there = (functions(was) if was is not None else None) or {}
                        added += [f"{path}: {name}" for name in names if name not in there]
                    elif functions(text) is None:
                        left.append(f"{path} cannot be read as Python")
                if left:
                    remarks.append(f"left as it is on the {EMPTIED}: {'; '.join(left)}")
            # Which tests the work wrote: among the names the written files declare, the fresh ones, new
            # under their name or with a body that is not the one the name had. A fresh test vouches for
            # itself and for no other: it may pass on emptied code only when it failed while it ran, by its
            # own name, on code without the work. An old test kept as it was answered for itself when it
            # was accepted, and a failure the output puts down to absent code vouches for nothing.
            def bodies_of(found: dict, name: str):
                return collections.Counter(body for dotted, all_of in found.items()
                                           if dotted.rpartition(".")[2] == name for body in all_of)

            fresh = set()                       # (the file as the runner names it, the test's bare name)
            for status, path in written:
                if not CODE.search(path):
                    continue
                now_text = show(source, revision, path) or ""
                old_text = (show(source, base, path) if status == "M" else show(source, frozen_ref, earlier(path))) or ""
                names = names_in(path, now_text, wide)
                mine = names - names_in(path, old_text, wide)
                if path.endswith(".py"):
                    then, now_functions = functions(old_text) or {}, functions(now_text) or {}
                    mine |= {name for name in names if bodies_of(then, name) != bodies_of(now_functions, name)}
                fresh |= {(relative(path, subdir), name) for name in mine}
            homes = {}                          # a test's name, and every written file that declares it fresh
            for rel, name in fresh:
                homes.setdefault(name, set()).add(rel)
            vouched, proof = set(), []
            for run in tried:
                told = named_failures(pathlib.Path(run["log"]).read_text(**TEXT))
                real = set()
                for name, ran, place in told:
                    if not ran or name not in homes:
                        continue
                    if place.endswith(".py"):   # the runner names the file: the test vouches for that file's.
                        # The file may be written from the stage folder or from the root of the repository,
                        # depending on where the runner anchors its paths; both exact forms are the same
                        # file. An ending is not the file: a deeper path must vouch nothing shallower.
                        hit = {rel for rel in homes[name] if place in (rel, f"{subdir}/{rel}" if subdir else rel)}
                    elif place:                 # a dotted id names the module the test stands in
                        hit = {rel for rel in homes[name]
                               if (place + ".").startswith(re.sub(r"\.py$", "", rel).replace("/", ".") + ".")}
                    else:                       # the runner names the test alone: one home, or none vouches
                        hit = homes[name] if len(homes[name]) == 1 else set()
                    if hit:
                        vouched.update((rel, name) for rel in hit)
                        real.add(name)
                if real:
                    proof.append(f"{', '.join(sorted(real)[:6])}{f' and {len(real) - 6} more' if len(real) > 6 else ''} "
                                 f"failed while they ran on the {run['tree']}")
                elif not told and run["tests_failed"]:
                    proof.append(f"{run['tests_failed']} test(s) failed while they ran on the {run['tree']}")
            if not written:
                remarks.append(f"the work wrote no test of its own: its test files are empty, or the ones accepted with "
                               f"the earlier stage, copied and unchanged. The run on the {EMPTIED} was not made")
            elif not emptied and left:
                remarks.append(f"the run on the {EMPTIED} was not made: every Python file the work changed outside its "
                               f"test files was left as it is")
            elif not emptied:
                remarks.append(f"the run on the {EMPTIED} applies to Python code only and was not made: the work "
                               f"changed no Python function outside the test files")
            else:
                limit = min(args.timeout, max(PATIENCE, round(10 * first["seconds"])))
                fourth = run_command(args.test_cmd, hollowed / subdir if subdir else hollowed, EMPTIED, limit, logs)
                runs.append(fourth)
                tried.append(fourth)
                what = ", ".join(emptied[:8]) + (f" and {len(emptied) - 8} more" if len(emptied) > 8 else "")
                if fourth["exit_code"] == 124 and fourth["seconds"] >= limit < args.timeout:
                    remarks.append(f"the run on the {EMPTIED} was stopped after {limit} s, where the posted revision took "
                                   f"{first['seconds']} s: tests that wait on code which no longer answers count as failing")
                fewer = first["tests_run"] is not None and (fourth["runner"] != first["runner"]
                                                            or (fourth["tests_run"] or 0) < first["tests_run"])
                hollow_tests = sorted({name for rel, name in fresh - vouched})
                if fourth["exit_code"] == 0 and fewer:      # what this tool did to the tree is no finding
                    remarks.append(f"the run on the {EMPTIED} exits 0 and ran {fourth['tests_run'] or 0} test(s) where "
                                   f"the posted revision ran {first['tests_run']}: what was emptied is needed to run the "
                                   f"tests, so this run shows nothing; emptied were {what}; log {fourth['log']}")
                elif fourth["exit_code"] == 0 and fresh and not hollow_tests and not added:
                    remarks.append(f"the run on the {EMPTIED} exits 0: no test notices that what the work changed returns "
                                   f"nothing ({what}). That is no finding here, because every test the work wrote vouched "
                                   f"for itself ({'; '.join(proof[:3])}), and every function that was emptied was already "
                                   f"there before the work: what such a test asks for may be that nothing happens; "
                                   f"log {fourth['log']}")
                elif fourth["exit_code"] == 0:
                    # A function the work added was on none of the trees above, so a test can fail there for
                    # merely finding it missing, in words no absence pattern knows: such a vouching is worth nothing.
                    more = f" and {len(added) - 8} more" if len(added) > 8 else ""
                    findings.append(f"passes with the code emptied: the delivered tests pass when every function the work "
                                    f"changed returns nothing ({what}); log {fourth['log']}" + (
                                        f". These tests of the work failed in no run on code without the work: "
                                        f"{', '.join(hollow_tests[:8])}"
                                        f"{f' and {len(hollow_tests) - 8} more' if len(hollow_tests) > 8 else ''}. "
                                        f"A test that can fail on no tree proves nothing: deliver it with a change "
                                        f"whose absence it can show, or strengthen it"
                                        if hollow_tests else "") + (
                                        f". The work added functions that no run above held, so a test may fail there "
                                        f"for finding them missing, and that vouches for nothing: {', '.join(added[:8])}{more}"
                                        if added else ""))
                elif fourth["tests_failed"]:
                    # The run is judged as a whole, so one test that notices covers every test that does not.
                    # Which of the delivered tests are not among the failures is said, and is no finding.
                    told = failures_in(pathlib.Path(fourth["log"]).read_text(**TEXT))
                    fresh_names = {name for rel, name in fresh}
                    real = sorted(name for name, ran in told.items() if ran and name in fresh_names)
                    if real:
                        proof.append(f"{', '.join(real[:6])}{f' and {len(real) - 6} more' if len(real) > 6 else ''} "
                                     f"failed while they ran on the {EMPTIED}")
                    elif not told:
                        proof.append(f"{fourth['tests_failed']} test(s) failed while they ran on the {EMPTIED}")
                    quiet = sorted(fresh_names - set(told))
                    remarks.append(f"on the {EMPTIED} {fourth['tests_failed']} of {fourth['tests_run']} test(s) failed; "
                                   f"emptied were {what}" + (
                                       f". Of the tests the work delivered, these are not named among the failures: "
                                       f"{', '.join(quiet[:8])}{f' and {len(quiet) - 8} more' if len(quiet) > 8 else ''}. "
                                       f"They passed on code that returns nothing, or did not run, and do not show that "
                                       f"their code is right" if quiet else "") + f"; log {fourth['log']}")
            for run in tried:
                if run["could_not_load"]:
                    remarks.append(f"on the {run['tree']} the tests could not load ({run['could_not_load']}): no test got "
                                   f"as far as failing, so this run does not show that the delivered tests can fail; "
                                   f"log {run['log']}")
            remarks.append("that the delivered tests can fail was shown by: " + "; ".join(proof) if proof else
                           "no run showed a delivered test that failed while it ran: where a run failed, the tests could "
                           "not load, the run was stopped, it printed no summary this tool knows, or what failed was not "
                           "named as a test of the work")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    verdict = {"card": "verdict", "stage": args.stage, "from": args.sender, "to": args.to, "revision": revision,
               "base": base or "empty", "runs": runs, "findings": findings,
               "verdict": "REJECT" if findings else "ACCEPT",
               "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    if notes:
        verdict["spec_change_notes"] = [f"{k}: {v}" for k, v in notes.items()]
    # What the room would have seen without this check: the command, run once more where it was written.
    plain = f"a plain re-run of the listed command on the posted revision exits {first['exit_code']}"
    if first.get("stopped_after"):
        plain = (f"the listed command was stopped by this tool on the posted revision after {first['stopped_after']} s "
                 f"(--timeout): a plain re-run has no such limit, and how it would end is not known")
    elif findings and first["exit_code"] == 0:
        plain += ": the findings are what a plain re-run does not show"
    verdict["notes"] = [plain] + remarks
    if frozen_ref:
        verdict["frozen_ref"], verdict["frozen_dir"] = frozen_ref, frozen_dir
    if emptied:
        verdict["emptied"] = emptied
    problems = cards.validate(verdict)
    if problems:
        raise CannotRun(f"internal: the verdict card is invalid: {problems}")
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(verdict, indent=1), encoding="utf-8")
    return verdict, (1 if findings else 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="check a posted revision on trees its author cannot edit")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--base", default="", help="the last accepted revision; while nothing is accepted, the revision "
                                               "the work started from, or `empty` when nothing came before it. "
                                               "Required, unless the ledger holds an accepted revision")
    ap.add_argument("--test-cmd", required=True)
    ap.add_argument("--stage", type=int, required=True)
    ap.add_argument("--subdir", default="")
    ap.add_argument("--changed-tests", default="", help="comma-separated test files the handoff lists as changed")
    ap.add_argument("--frozen-ref", default="", help="the accepted revision whose tests are frozen; left out, the ledger "
                                                     "names the last revision accepted for an earlier stage")
    ap.add_argument("--frozen-dir", default=None, help="folder holding the frozen tests at --frozen-ref (default: --subdir, "
                                                       "or with a ledger the stage folder of that earlier stage)")
    ap.add_argument("--spec-change", action="append", help='"path=why the requirement changed"; repeatable')
    ap.add_argument("--out", default="")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--from", dest="sender", default="falsifier")
    ap.add_argument("--to", default="implementer")
    ap.add_argument("--ledger", default="", help="a file of JSON lines kept by this tool, one line per verdict: it names "
                                                 "the base and the frozen tests, and a base it does not hold as the "
                                                 "last accepted revision is refused")
    args = ap.parse_args(argv)
    out = pathlib.Path(args.out) if args.out else None
    try:
        if out is not None:
            set_aside(out)
        number = re.search(r"\d+$", args.subdir.strip("/"))
        if number and int(number.group()) != args.stage:
            raise Refused(f"--stage {args.stage} is not the stage of the folder {args.subdir}, which ends in "
                          f"{int(number.group())}: the verdict would stand under the wrong stage, and the frozen tests "
                          f"would be looked for in the wrong folder. Give the stage that the folder holds")
        args.taken = settle(args)
        if not args.base:                   # said the way the parser says it, and with its exit code
            ap.error("the following arguments are required: --base" + (
                f" (the ledger {args.ledger} holds no accepted revision yet, so it names none)" if args.ledger else ""))
        verdict, code = examine(args)
        if args.ledger:
            record(args.ledger, verdict)
    except Refused as exc:
        print(f"refused: {exc}. Nothing was checked and no verdict was written.")
        return 2
    except Exception as exc:                # an error in this tool is never a rejection
        if out is not None:                 # and a verdict the ledger could not take is none
            out.unlink(missing_ok=True)
        said = str(exc) if isinstance(exc, CannotRun) else f"{type(exc).__name__}: {exc}"
        print(f"could not run the check: {said}. No verdict was written.")
        return 3
    print(cards.render(verdict))
    return code


if __name__ == "__main__":
    sys.exit(main())
