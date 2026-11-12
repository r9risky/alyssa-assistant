# Drawn animation frames (optional)

Alyssa animates her two base pictures in code. Drawn frames make her more
expressive, and they drop in with no code changes: add PNGs to a folder below
and she plays them in name order (8 frames a second) whenever she is in that
state. The code motion (tilt, squash) still plays on top.

| Folder   | When it plays                               | Suggested frames |
| -------- | ------------------------------------------- | ---------------- |
| `idle/`  | Hanging out (include a blink partway through) | 8–16 |
| `talk/`  | Speaking (mouth shapes: closed, half, open)  | 4–6 |
| `sleep/` | Napping after a while with no activity       | 4–8 |
| `drag/`  | Being dragged around (flailing)              | 4–6 |
| `land/`  | Just dropped (squash, bounce back)           | 3–5 |
| `music/` | Music is playing (swaying)                   | 8 |

Tips:
- Same canvas size for every frame in every folder, transparent background,
  feet at the same spot near the bottom edge.
- Name them so they sort: `000.png`, `001.png`, ...
- A folder with no PNGs falls back to her base pictures, so add states one at a time.
- Use your own or commissioned art only, never art from other projects.
