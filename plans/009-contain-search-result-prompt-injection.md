# Plan 009: Keep untrusted search results away from side-effecting tools

> **Executor instructions**: Implement a capability boundary, not keyword-based
> prompt filtering. Update the plan index when done.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `plugins/web_search.py:59-65,126-135` and `brain.py:2540-2660` still match.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plan 008
- **Category**: security
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

Remote search titles and snippets are fed into a second model turn that retains
the full desktop-control tool registry. An attacker-controlled snippet can issue
instructions that induce typing, key presses, file/application operations, or
other unconfirmed side effects. Treating the text as “untrusted” in prose is not
enough; the synthesis turn must lack dangerous capabilities.

## Current state

- `plugins/web_search.py:59-65` accepts remote title, description, and URL after
  basic HTML cleanup.
- `plugins/web_search.py:126-135` formats those fields into a tool result.
- `brain.py:2605-2614` appends tool output as model-visible content.
- `brain.py:2648-2657` deliberately performs another model round after search.
- `brain.py:2569-2583` dispatches against the full `actions.FUNCTIONS` registry.
- Provider tool-schema conversion is centralized in `brain.py`; match its cached
  conversion pattern rather than implementing provider-specific ad hoc filters.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.\.venv\Scripts\python.exe -m unittest tests.test_untrusted_tool_results -v` | all pass |
| Full tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Syntax | `.\.venv\Scripts\python.exe -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('.').rglob('*.py') if '.venv' not in p.parts and 'plans' not in p.parts]"` | exit 0 |

## Scope

**In scope**: `brain.py`, `plugins/web_search.py` only for explicit provenance
metadata if needed, `tests/test_untrusted_tool_results.py` (new), and a short
README security note.

**Out of scope**: trying to sanitize natural-language snippets, changing search
providers, disabling search, or weakening confirmation for protected tools.

## Git workflow

If git exists, branch `advisor/009-untrusted-search-boundary`. Do not push.

## Steps

### Step 1: Represent untrusted-result provenance

Track that the conversation now contains remote search material using internal
dispatcher state, not model-controlled fields. The state must survive provider
message conversion and clear after the constrained synthesis response or a fresh
user turn.

**Verify**: focused tests prove provenance is set only by actual `search_web`
execution and cannot be forged through ordinary tool output text.

### Step 2: Add a constrained synthesis call

Allow `_call_model` and provider adapters to receive an explicit allowed-tool
set. For the post-search turn, supply no side-effecting tools; preferably supply
no tools at all. If a read-only follow-up is essential, define a tiny named
allowlist and document why each member is side-effect free. Validate returned
tool calls at dispatch too—schemas are not an authorization boundary.

**Verify**: provider-conversion tests show the restricted declarations, and a
forged disallowed call returns a safe error without invoking the function.

### Step 3: Preserve the intended spoken summary

Keep the existing search-summary behavior and source naming. If the model asks
to perform a desktop action based on search content, tell the user it needs a
fresh explicit request; do not carry the untrusted instruction forward.

**Verify**: test a benign result and instruction-shaped hostile title/snippet.
Both produce text; neither can execute a mocked side-effecting tool.

## Test plan

- Cover Gemini, OpenAI-compatible, Anthropic, and Ollama normalized flows.
- Include a hostile snippet requesting typing, command execution, URL opening,
  and confirmation forgery; assert zero mock calls.
- Cover a fresh subsequent user request regaining the normal tool registry.
- Model provider-message assertions after `tests/test_brain_message_conversion.py`.

## Done criteria

- [ ] Post-search synthesis has no side-effecting tools.
- [ ] Dispatch enforces the allowlist even if a provider returns an undeclared call.
- [ ] Normal direct user turns retain existing capabilities.
- [ ] All provider paths and the full suite pass.
- [ ] No natural-language blacklist is used as the primary control.

## STOP conditions

- A provider adapter cannot express a restricted/no-tool call without changing
  its public API behavior broadly.
- Search results bypass the central dispatcher through another live path.
- The implementation needs to trust a provenance field supplied by the model.

## Maintenance notes

Apply the same capability restriction to future tools that ingest webpages,
emails, documents, or other attacker-controlled content before a model turn.

