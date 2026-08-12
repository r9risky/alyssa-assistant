# Plan 012: Block private-network and redirect SSRF in webpage summarization

> **Executor instructions**: Validate destinations at every redirect hop. Do not
> include probe URLs or exploitation instructions in tests or documentation.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `plugins/web_summarizer.py:24-38,71-76` still matches.

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: plan 009
- **Category**: security
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

The summarizer fetches arbitrary HTTP(S) input with redirects enabled and
returns response content to the model. A generated or injected URL can reach
loopback, link-local, reserved, or private-network services from the user's PC.
The fetcher needs explicit scheme, DNS/IP, redirect, size, and content controls.

## Current state

- `plugins/web_summarizer.py:29-31` prepends HTTPS when no scheme exists.
- `plugins/web_summarizer.py:34` calls `requests.get()` with default redirect
  following and no address validation or response-size limit.
- `plugins/web_summarizer.py:71-76` truncates parsed text only after the complete
  response has already been downloaded.
- The existing test only checks Beautiful Soup availability.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_web_summarizer_safety -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Syntax | `.\.venv\Scripts\python.exe -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('.').rglob('*.py') if '.venv' not in p.parts and 'plans' not in p.parts]"` | exit 0 |

## Scope

**In scope**: `plugins/web_summarizer.py`, `tests/test_web_summarizer_safety.py`
(new), and README documentation for public-only default behavior.

**Out of scope**: general-purpose browser automation, arbitrary intranet support,
search-result injection (plan 009), or adding a proxy service.

## Git workflow

If git exists, branch `advisor/012-web-fetch-policy`. Do not push.

## Steps

### Step 1: Add destination validation

Parse with `urllib.parse`; allow only `http` and `https`; reject credentials in
URLs, missing hosts, malformed ports, and non-HTTP schemes. Resolve all host
addresses and reject loopback, private, link-local, multicast, unspecified,
reserved, and otherwise non-global targets using `ipaddress`.

**Verify**: table-driven tests cover public IPv4/IPv6, disallowed address
classes, mixed DNS results, malformed URLs, and userinfo.

### Step 2: Validate redirects manually

Disable automatic redirects. Accept only a small bounded number of redirects,
resolve relative `Location` values, and rerun the complete destination check
before every request. Pin the validated address/host relationship in a way that
does not reopen a DNS rebinding window; if safe pinning is not feasible with the
current HTTP stack, STOP and propose a vetted library/design.

**Verify**: mocked public-to-private redirects are rejected before the second
request; public chains within the limit succeed.

### Step 3: Bound response handling

Stream with a strict byte limit, reject unsupported content types, and use
bounded connect/read timeouts. Preserve the current 2,000-character extracted
snippet only after safe download and parsing.

**Verify**: oversized, non-HTML, timeout, redirect-loop, and valid HTML tests pass.

### Step 4: Document an explicit intranet opt-in decision

Default to public internet only. If maintainers later need intranet pages, they
must add a clearly scoped allowlist setting; do not silently permit private ranges.

**Verify**: README states public-only default and no bypass is enabled by default.

## Test plan

- Mock DNS and HTTP; never contact real internal or public hosts.
- Assert rejected destinations yield a short user-safe explanation without
  echoing sensitive response content.
- Keep existing Beautiful Soup tests passing.

## Done criteria

- [ ] Every request and redirect destination is validated.
- [ ] Non-global targets and mixed DNS answers fail closed.
- [ ] Downloads have redirect, byte, type, and timeout limits.
- [ ] Focused and full suites pass without network access.

## STOP conditions

- DNS-to-socket pinning cannot be implemented safely with the chosen approach.
- Existing product requirements explicitly require arbitrary intranet URLs.
- A new dependency would be required but cannot be reviewed or pinned.

## Maintenance notes

Reuse this fetch policy for future URL-reading plugins. URL scheme checks alone
are insufficient; redirects and DNS resolution are part of the boundary.

