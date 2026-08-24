"""Static built-in tool schemas exposed to language-model providers.

Keeping schemas separate from dialogue orchestration makes the reasoning module
smaller and lets tooling evolve without mixing it with conversation state.
"""

BASE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": (
                "Open an application on Windows by name, e.g. 'notepad', "
                "'spotify', 'chrome'. Also use this for vague/indirect "
                "requests that clearly imply opening something, even "
                "without naming an app - e.g. 'I need to jot something "
                "down' -> notepad, 'I want to browse the web' -> a "
                "browser, 'I want to check my email' -> outlook. For "
                "music requests ('put some music on', 'play some jazz'), "
                "use play_music instead of this one. Guess the most "
                "sensible app for the intent."
            ),
            "parameters": {
                "type": "object",
                "properties": {"app_name": {"type": "string"}},
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type_text",
            "description": "Type text at the current cursor location, wherever focus currently is.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "press_keys",
            "description": (
                "Press a keyboard shortcut, e.g. 'ctrl+s', 'alt+tab', "
                "'win+d'. Also use this for vague requests that clearly "
                "map to a well-known shortcut even if the user doesn't "
                "name it - e.g. 'save this' -> ctrl+s, 'undo that' -> "
                "ctrl+z, 'copy this' -> ctrl+c, 'find on this page' -> "
                "ctrl+f."
            ),
            "parameters": {
                "type": "object",
                "properties": {"keys": {"type": "string"}},
                "required": ["keys"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": (
                "Open a website URL in the default web browser, e.g. "
                "'youtube.com', 'https://google.com/search?q=cats'. Also "
                "use this for requests that explicitly ask to open, "
                "browse to, or search the web/Google/a specific site - "
                "'look that up online', 'search the web for X', 'google "
                "X', 'pull up X's website'. Build a search URL (e.g. "
                "'https://google.com/search?q=X') for those. Do NOT use "
                "this for ordinary factual or trivia questions the user "
                "is just asking out loud - 'who is Spider-Man', 'what's "
                "the capital of France', 'how tall is the Eiffel Tower', "
                "'how many ounces in a cup'. Those get answered directly "
                "in your own spoken reply, from what you already know, "
                "with NO tool call at all - exactly like a knowledgeable "
                "person would just answer rather than grabbing a browser. "
                "Opening a browser window steals focus from whatever the "
                "user is doing (e.g. a game), so only open one when they "
                "clearly asked to open/browse/search something themselves "
                "- not merely asked a question you can answer yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": (
                "Open a specific file by its full path with its default "
                "application, e.g. 'C:\\Users\\me\\Documents\\notes.txt'. "
                "Also use for vague requests naming a specific document by "
                "topic rather than a path - e.g. 'pull up my resume' or "
                "'open that budget spreadsheet' - resolve the path using "
                "remembered facts if you have one, or call search_files "
                "first if you don't know where it lives."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file or folder by its full path. Moves it to the Recycle Bin, so it's recoverable.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a Windows command-line command and return its output. Use for anything not covered by the other tools.",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Searches for files by filename (substring match) on the "
                "user's PC, e.g. 'find that invoice pdf' or 'search for "
                "files with budget in the name'. Defaults to searching the "
                "user's home folder if no location is given. Can optionally "
                "also search inside small text files for matching content, "
                "not just the filename, if the user asks to search by "
                "content/text rather than by file name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for."},
                    "location": {
                        "type": "string",
                        "description": "Folder to search under, e.g. 'Documents' or 'C:\\Users\\me\\Desktop'. Leave blank to search the user's home folder.",
                    },
                    "search_contents": {
                        "type": "boolean",
                        "description": "True to also search inside file contents, not just filenames. Defaults to false.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_fact",
            "description": (
                "Save a fact to permanent memory so it's remembered in "
                "future conversations, even after restarting. Use this "
                "whenever the user tells you something worth remembering "
                "long-term, like their name, a preference, or a recurring "
                "detail. Also use it proactively when you resolve a vague "
                "or personal request by inferring what the user meant "
                "(e.g. they said 'put my music on' and you guessed "
                "Spotify, or 'open the office folder' and you guessed a "
                "specific path) - saving that mapping means the next "
                "vague request like it resolves faster and more "
                "accurately, without needing to ask or re-guess."
            ),
            "parameters": {
                "type": "object",
                "properties": {"fact": {"type": "string"}},
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_fact",
            "description": "Remove a previously remembered fact that matches the given text, e.g. if the user says to forget something or it's no longer true.",
            "parameters": {
                "type": "object",
                "properties": {"fact_snippet": {"type": "string"}},
                "required": ["fact_snippet"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_music",
            "description": (
                "Plays music via Spotify (default) or YouTube Music. Use "
                "for any request to play/put on music - e.g. 'play some "
                "jazz', 'put on Bohemian Rhapsody', 'play my Discover "
                "Weekly', 'play some lofi on YouTube Music', or just "
                "'play some music' with no specifics. Tries the desktop "
                "app first; if it isn't installed, falls back to opening "
                "the service in the browser instead - either way, once "
                "something's playing, media_play_pause/next/previous and "
                "the volume tools control it normally."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Song, artist, album, or playlist to search for. Leave blank to just open/resume whatever's already cued up.",
                    },
                    "service": {
                        "type": "string",
                        "description": "'spotify' (default) or 'youtube music'.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_play_pause",
            "description": "Toggles play/pause on whatever media is currently playing (Spotify, YouTube, etc.). Also use for vague requests like 'pause that' or 'stop the music'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_next_track",
            "description": "Skips to the next media track. Also use for vague requests like 'skip this song', 'next one', or 'I don't like this song'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_previous_track",
            "description": "Goes back to the previous media track. Also use for vague requests like 'go back a song' or 'replay the last one'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "volume_up",
            "description": "Turns the system volume up. Also use for vague requests like 'turn it up', 'louder', or 'I can't hear it'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "integer",
                        "description": "How many volume-up presses, roughly 2% each. Defaults to 2.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "volume_down",
            "description": "Turns the system volume down. Also use for vague requests like 'turn it down', 'quieter', or 'that's too loud'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "integer",
                        "description": "How many volume-down presses, roughly 2% each. Defaults to 2.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_mute",
            "description": "Mutes or unmutes system audio. Also use for vague requests like 'mute that', 'silence it', or 'I need it quiet'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume_level",
            "description": "Sets system audio volume to a specific percentage (0 to 100%), e.g. 'set volume to 50%', 'volume 80'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {
                        "type": "integer",
                        "description": "Target volume level percentage from 0 to 100.",
                    }
                },
                "required": ["percent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "minimize_window",
            "description": "Minimizes the currently focused window. Also use for vague requests like 'get this out of the way' or 'hide this'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "maximize_window",
            "description": "Maximizes the currently focused window. Also use for vague requests like 'make this bigger' or 'full screen this'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "close_window",
            "description": "Closes the currently focused window/application (alt+F4). Also use for vague requests like 'get rid of this', 'close this out', or 'I'm done with this'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_window",
            "description": "Switches focus to the previously active window (alt+tab). Also use for vague requests like 'switch to my other window' or 'go back to what I was doing'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "snap_window",
            "description": (
                "Snaps the currently focused window to one side of the "
                "screen. Also use for vague requests like 'put this on the "
                "left' or 'move this window over so I can see something "
                "else' - default to 'left' if a side isn't specified."
            ),
            "parameters": {
                "type": "object",
                "properties": {"side": {"type": "string", "description": "'left' or 'right'."}},
                "required": ["side"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_desktop",
            "description": "Minimizes all windows to show the desktop. Also use for vague requests like 'clear my screen' or 'get everything out of the way'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_clipboard",
            "description": "Reads and returns the current clipboard text content. Also use for vague requests like 'what did I just copy' or 'what's on my clipboard'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_clipboard",
            "description": "Copies the given text to the clipboard.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Takes a screenshot of the whole screen and saves it to the Pictures folder. Also use for vague requests like 'capture my screen' or 'grab a picture of this'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_screen",
            "description": (
                "Look at what's on the user's screen right now and "
                "describe it, or answer a question about it - e.g. "
                "'what am I looking at', 'what's on my screen', 'does "
                "this code look right', 'who is this (character/person/"
                "actor) on my screen', 'what game/show is this'. Use this "
                "for ANY question asking to identify, name, or explain "
                "something visible on screen - even if phrased as 'who "
                "is this' rather than 'what's on my screen' - since that "
                "always means the content being displayed, never Alyssa "
                "herself (see run_diagnostics for questions about her). "
                "Captures a fresh screenshot each time; nothing is saved "
                "to disk (unlike take_screenshot)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Their specific question about the screen, if any. Blank = general description.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_screen_element",
            "description": (
                "Clicks a described element currently visible on screen - "
                "e.g. 'click the Send button', 'click the X to close that', "
                "'click on the search bar'. Uses the same vision look at the "
                "screen as describe_screen, so it works on anything visibly "
                "on screen, not just known UI. Always confirmed first since "
                "a click could do anything. For typing after clicking a "
                "field (e.g. 'click the search bar and type cats'), call "
                "this first, then type_text as a follow-up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What to click, described the way a person would point it out, e.g. 'the blue Submit button'.",
                    },
                    "double_click": {
                        "type": "boolean",
                        "description": "True for a double-click (e.g. opening a desktop icon). Defaults to false.",
                    },
                },
                "required": ["description"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_datetime",
            "description": "Returns the current local date and time - use this instead of guessing when the user asks what time or day it is.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_diagnostics",
            "description": (
                "Runs a self-check across everything Alyssa depends on - "
                "her LLM connection, speech recognition, microphone, "
                "text-to-speech, persistent memory, and plugins - and "
                "reports whether each is healthy. Use this ONLY when the "
                "user is asking about Alyssa's own health/systems - e.g. "
                "'is everything working?', 'are you okay?', or 'run a "
                "health check'. Never use this for a question about what "
                "is visible on the user's screen (use describe_screen for "
                "that instead), even if the question happens to include "
                "her name or the words 'is this'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_alyssa",
            "description": (
                "Restarts Alyssa herself, not the computer. Use when the user asks "
                "to restart, relaunch, or reboot Alyssa or the assistant app."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_power_action",
            "description": (
                "Locks, sleeps, signs out, restarts, or shuts down the PC. Also use "
                "for vague requests that clearly imply one of these - "
                "e.g. 'I'm stepping away' or 'lock it up' -> lock, 'I'm "
                "done for now' -> sleep. Only use restart/shutdown when "
                "clearly and explicitly requested, since they close every "
                "open app and can lose unsaved work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "One of: 'lock', 'sleep', 'signout', 'restart', 'shutdown'.",
                    }
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reset_conversation",
            "description": "Clears short-term conversation memory - use when the user asks to start fresh, change the subject, or forget the current conversation.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]
