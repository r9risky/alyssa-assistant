# Plan 006: Run startup at normal user privilege

> **Executor instructions**: Follow the steps and verification gates exactly.
> Update `plans/README.md` when complete. Do not invent a privileged broker in
> this plan.
>
> **Drift check (run first)**: no git metadata is present. Confirm
> `install_startup.bat:33-38,57-61` and `plugin_loader.py:56-66` still match.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: MED
- **Depends on**: none
- **Category**: security
- **Planned at**: no git metadata available, 2026-08-13

## Why this matters

The login task launches project-local code with `RunLevel Highest`, while the
assistant dynamically executes editable plugins. A normal user-level write to
the project, venv, executable, or plugin directory can therefore become
administrator-level execution at next login. Alyssa should start with the same
privilege as the signed-in user; individual elevated operations can be designed
later behind a narrow broker if a real requirement remains.

## Current state

- `install_startup.bat:33-38` chooses `dist\Alyssa.exe` or the local venv's
  `pythonw.exe main.py`.
- `install_startup.bat:57-61` builds a scheduled-task principal with
  `-RunLevel Highest` and registers it with `-Force`.
- `plugin_loader.py:56-66` executes each enabled project-local `.py` plugin.
- README's startup section explicitly promises admin rights.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Inspect task | `powershell -NoProfile -Command "Get-ScheduledTask -TaskName AlyssaAssistant | Select-Object -ExpandProperty Principal | Format-List UserId,RunLevel,LogonType"` | `RunLevel : Limited` after installation |
| Tests | `.\.venv\Scripts\python.exe -m unittest discover -v` | all pass |
| Syntax | `.\.venv\Scripts\python.exe -c "import ast,pathlib; [ast.parse(p.read_text(encoding='utf-8')) for p in pathlib.Path('.').rglob('*.py') if '.venv' not in p.parts and 'plans' not in p.parts]"` | exit 0 |

## Scope

**In scope**: `install_startup.bat`, `uninstall_startup.bat` if wording needs
alignment, README startup/security text, and a non-destructive validation script
or test fixture if useful.

**Out of scope**: changing plugin execution, adding a service, privileged broker,
installer, ACL manipulation, or altering individual action behavior.

## Git workflow

If a git checkout is used, branch `advisor/006-least-privilege-startup`. Do not
push or create a PR unless instructed.

## Steps

### Step 1: Register a limited scheduled-task principal

Remove self-elevation and use a normal interactive user principal with
`RunLevel Limited`. Preserve the existing executable selection, working
directory, battery behavior, and clear error messages. Installation must not
trigger UAC.

**Verify**: run `install_startup.bat`, then the task-inspection command; it exits
0 and prints `RunLevel : Limited`.

### Step 2: Make the trust boundary explicit

Update README and script comments to say startup is unelevated. Explain that
actions targeting elevated processes may fail and that users should not run the
whole assistant as administrator, because it loads editable plugins and model-
selected actions.

**Verify**: `rg -n "highest privileges|admin rights|elevated" README.md install_startup.bat`
returns no claim that startup runs elevated, except an explicit warning not to.

### Step 3: Exercise install, run, and uninstall

From a disposable test task name or a safe test machine, install the task, start
it, confirm it launches from the intended working directory as the current user,
then remove it with the existing uninstaller.

**Verify**: task principal is Limited; uninstall leaves
`Get-ScheduledTask -TaskName AlyssaAssistant` reporting not found.

## Test plan

- Validate the generated principal and action without editing unrelated tasks.
- Run the Python suite to ensure documentation/script changes did not affect code.
- Manually smoke-test both exe and venv branches when available.

## Done criteria

- [ ] Startup installation never asks for elevation.
- [ ] The registered task uses `RunLevel Limited`.
- [ ] Editable plugins never run inside an auto-elevated assistant process.
- [ ] README matches actual privilege behavior.
- [ ] Existing tests and syntax checks pass.

## STOP conditions

- Windows refuses to create a limited interactive task with the current design.
- A documented, essential capability is proven to require whole-process elevation.
- The task targets a machine-managed, administrator-owned installation rather
  than the project-local paths cited above.

## Maintenance notes

Do not reintroduce `RunLevel Highest` as a convenience fix. A future privileged
broker must accept a narrow typed command set and must never import plugins or
user-writable Python configuration.

