# The check list

One line of JSON per requirement sentence, in a file such as `checks/stage-1.jsonl`. The checking seat
writes it from the written requirements, never from any test files that were handed out, and never from
the source. `speccheck.py` runs it against the served service and prints how many sentences have a check.

## An entry

| Field | Meaning |
|---|---|
| `id` | short and stable; cite where the sentence lives, for example `S1-7.4` for stage 1, section 7, fourth rule |
| `sentence` | the requirement, quoted word for word: with `--spec` a sentence that stands in no requirements file makes the list unusable |
| `kind` | `literal` (names, codes, statuses), `behaviour` (what happens over several requests) or `concurrency` (what holds when requests arrive together) |
| `setup` | optional steps run first, such as a reset and a sign-in |
| `steps` | requests run in order, each with what is expected |
| `burst` | requests released together, with what is expected of them as a group |
| `readback` | steps run after a burst, reading state through the public interface |
| `no_readback` | instead of `readback`: the reason, in two words or more, why nothing can be read after the burst |
| `unchecked` | instead of steps or a burst: the reason this sentence has no executable check |

Every sentence gets an entry. A sentence that cannot be checked is listed with `unchecked`, so the coverage
line stays honest.

## Quoting a sentence

Copy the sentence from the requirements file. Capitals, punctuation, dashes and quotation marks have to be
the ones of the file. Leave out what Markdown adds: the marker of a list item, heading marks, emphasis and
back-ticks. A final full stop may be left out; one that the file does not have may not be added. Quote a row
of a table cell by cell, or as the whole row with its bars. Sentences that follow one another may be quoted
together, over five lines at most.

A part of a sentence is found and keeps the list usable. It gets a note and counts for no line of the
requirements, so quote the whole sentence.

## A step

```json
{"request": {"method": "POST", "path": "/things", "headers": {"Authorization": "Bearer {{token}}"}, "json": {"n": 1}},
 "expect": {"status": 201, "json": {"$.id": {"type": "string"}, "$.n": 1}},
 "save": {"thing": "$.id"}}
```

`expect` may hold:
- `status`: a number or a list of allowed numbers
- `json`: a path and either the exact value or one matcher: `{"type": "string|integer|number|boolean|array|object|null"}`,
  `{"exists": true|false}`, `{"regex": "..."}`, `{"ne": value}`, `{"len": n}`, `{"in": [..]}`, `{"eq": value}`.
  A number in a burst expectation may be a saved value, written `"{{name}}"`, if a step of the entry saves it.
  To compare with an object whose only fields are matcher names, wrap it: `{"eq": {"type": "admin"}}`
- `header`: a header name and a pattern; write `.+` to ask only that the header is there
- `testids`: markers that must appear in the served page
- `body_empty`: true · `body_contains`: a list of strings

`save` keeps a value from the response. `{{name}}` uses it later, in a request or in an expectation; a string
that is only `{{name}}` keeps the value's type, so a saved number stays a number.

Every step under `steps` and `readback` needs an `expect` that checks something. A step that expects nothing,
or whose expectation is empty or cannot fail, would pass whatever the service answers, so the list is refused.
A step under `setup` needs `expect` or `save`: a bare request, such as a reset nobody looks at, is refused.

Write no value shaped like a credential into the list: the list is committed, and a public repository is
scanned for those shapes. For a real token, save it from a response and write `{{name}}`. For a made-up one,
keep it under 20 characters or without a digit, for example `Bearer not-a-real-token`.
A request path starts with `/` and holds no space. A path into an answer is written `$.name`,
`$.list[0].name`, `name` or `$` for the whole answer. An unknown field anywhere is refused, so a misspelt name cannot
quietly switch a check off.

## A burst

```json
{"n": 10, "request": {"method": "POST", "path": "/claims", "json": {"who": "{{i}}"}},
 "expect": {"status_counts": {"201": 1, "409": 9}}}
```

All requests wait on a barrier and are released together, at most 50 at once; each then connects and sends,
as a caller would. `{{i}}` counts from 0 and `{{i1}}` from 1. Use `requests` with a list instead of `n` when
the requests differ, for example different callers racing for one thing.

An entry of kind `concurrency` that holds a burst needs a `readback`. Requests under `steps` do not count
as one. Where nothing can be read afterwards, write `no_readback` with the reason. That is a waiver and no
check: a service that tells every caller 200 and loses updates passes such an entry. The coverage line names
every entry whose burst is followed by no read-back. A burst of another kind needs a `readback`, or an
`expect` that checks something.

`expect` may hold:
- `status_counts`: the exact tally · `max_status_counts` and `min_status_counts`: bounds per status
- `distinct`: a path whose values must not repeat across successful responses; with no successful response
  there is nothing to compare, and the entry fails
- `values_are_range`: `{"path": "$.value", "from": 8, "to": 27}`, each value once
- `identical_bodies`: true, when every successful response must be the same
- `no_5xx`: true by default; a transport error counts as a failure

A burst that passes is run three times in a row before it counts: a race that did not open proves nothing.
Always read state back afterwards. Status codes say what the service claimed; the read-back says what it did.

## Findings

Each failing entry becomes a finding card in the cards folder, written the moment the entry fails and holding
the exact requests. The card closes only when `speccheck.py replay <card> --base-url <url>` passes. A summary
or a claim never closes a card, and a card that holds no request or no expectation cannot be replayed.
A card whose check misread the requirements is withdrawn with `speccheck.py withdraw <card> --reason "..."`.
It stays in the folder with its reason, and it is counted as withdrawn, not as closed.
If the service cannot be reached the tool exits with 3: an error is not a finding. Cards written for earlier
entries are kept. A saved value that was used as a credential is written back as `{{name}}`, and anything
shaped or named like a credential is masked. Read a card before you commit it all the same: `README.md` says
what the masking cannot see.

## What a page scan cannot see

`testids` reads the page as served. Markers that a script adds after loading need a browser; list those
sentences as `unchecked` and say so, or drive a browser if one is installed beside the seat.
