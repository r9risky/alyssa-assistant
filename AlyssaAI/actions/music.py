import os
import time
import urllib.parse
import webbrowser

from .desktop import pyautogui
import requests

import config

from .apps_and_files import _resolve_app_path
from .confirmation import _confirm

_SPOTIFY_TOKEN_URL = "https://accounts.spotify.com/api/token"


_SPOTIFY_SEARCH_URL = "https://api.spotify.com/v1/search"


_spotify_token_cache = {"access_token": None, "expires_at": 0}


def _get_spotify_token():
    """Returns a valid app-only Spotify access token, fetching/caching a new
    one if needed, or None if credentials aren't configured or the request
    fails. This token can only read public catalog data (search, track/
    album/artist/playlist info) - it has no access to any user's account,
    playlists, or listening history, since play_music never asks anyone to
    log in."""
    client_id = getattr(config, "SPOTIFY_CLIENT_ID", "")
    client_secret = getattr(config, "SPOTIFY_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None

    if _spotify_token_cache["access_token"] and time.time() < _spotify_token_cache["expires_at"]:
        return _spotify_token_cache["access_token"]

    try:
        response = requests.post(
            _SPOTIFY_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"[play_music] Spotify auth failed ({e}); falling back to search link")
        return None

    token = data.get("access_token")
    if not token:
        return None
    # Refresh a little early (60s of slack) rather than risk a request
    # landing right as the cached token expires.
    _spotify_token_cache["access_token"] = token
    _spotify_token_cache["expires_at"] = time.time() + data.get("expires_in", 3600) - 60
    return token


def _spotify_top_match(query: str):
    """Searches Spotify's catalog for `query` and returns (uri, label) for
    the single best match, or None if there's no token available or nothing
    was found. Tries tracks first (the common case - "play X"), then falls
    back to albums/artists/playlists so a request like "play my Discover
    Weekly" or "play some Fleetwood Mac" still resolves to something
    playable instead of only ever matching individual songs."""
    token = _get_spotify_token()
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    for search_type, label_fmt in (
        ("track", lambda item: f"{item['name']} by {item['artists'][0]['name']}" if item.get("artists") else item["name"]),
        ("album", lambda item: f"the album {item['name']} by {item['artists'][0]['name']}" if item.get("artists") else f"the album {item['name']}"),
        ("playlist", lambda item: f"the playlist {item['name']}"),
        ("artist", lambda item: item["name"]),
    ):
        try:
            response = requests.get(
                _SPOTIFY_SEARCH_URL,
                headers=headers,
                params={"q": query, "type": search_type, "limit": 1},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"[play_music] Spotify search failed ({e}); falling back to search link")
            return None

        items = (data.get(f"{search_type}s") or {}).get("items") or []
        # Spotify's API can return a null slot in results for content that's
        # been taken down/region-locked - skip past those instead of
        # treating a null as a match.
        item = next((i for i in items if i), None)
        if item and item.get("uri"):
            try:
                return item["uri"], label_fmt(item)
            except (KeyError, IndexError):
                return item["uri"], item.get("name", query)

    return None


def _spotify_uri_to_web_url(uri: str):
    """Converts a 'spotify:track:ID'-style URI into the equivalent
    'https://open.spotify.com/track/ID' web player link, for when the
    desktop app isn't installed - open.spotify.com starts playing a track/
    album/playlist/artist page directly, same as the app does with the URI."""
    parts = uri.split(":")
    if len(parts) != 3:
        return None
    _, kind, item_id = parts
    return f"https://open.spotify.com/{kind}/{item_id}"


_YOUTUBE_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


def _youtube_top_video(query: str):
    """Searches YouTube for `query` and returns (video_id, title) for the
    single best match, or None if no API key or no match. Prefers Music
    category (id 10) results first so "play some jazz" doesn't land on an
    unrelated talk-show clip, falling back to unfiltered search if empty."""
    api_key = getattr(config, "YOUTUBE_API_KEY", "")
    if not api_key:
        return None

    for extra_params in ({"videoCategoryId": "10"}, {}):
        try:
            response = requests.get(
                _YOUTUBE_SEARCH_URL,
                params={
                    "part": "snippet",
                    "q": query,
                    "type": "video",
                    "maxResults": 1,
                    "key": api_key,
                    **extra_params,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"[play_music] YouTube search failed ({e}); falling back to search link")
            return None

        items = data.get("items") or []
        item = next((i for i in items if i and (i.get("id") or {}).get("videoId")), None)
        if item:
            video_id = item["id"]["videoId"]
            title = (item.get("snippet") or {}).get("title", query)
            return video_id, title

    return None


def _launch_resolved_music(service_key: str, query: str, app_path):
    """Tries to resolve `query` to one specific piece of content and start
    it playing directly, for whichever service_key this is. Returns the
    spoken reply on success, or None if there's no API access configured,
    nothing matched, or launching it failed - in which case play_music()
    falls back to a plain search-results page instead."""
    if service_key == "spotify":
        match = _spotify_top_match(query)
        if not match:
            return None
        uri, label = match
        if app_path:
            try:
                os.startfile(uri)
                return f"Playing {label} on Spotify."
            except OSError as e:
                print(f"[play_music] opening resolved Spotify URI failed ({e}); trying the web player instead")
        web_url = _spotify_uri_to_web_url(uri)
        if not web_url:
            return None
        webbrowser.open(web_url)
        return f"Playing {label} on Spotify in your browser."

    if service_key == "youtube music":
        match = _youtube_top_video(query)
        if not match:
            return None
        video_id, title = match
        # No registered desktop-app URI scheme for YouTube Music, so this
        # always opens as a browser watch link, which starts playing on its own.
        webbrowser.open(f"https://music.youtube.com/watch?v={video_id}")
        return f"Playing {title} on YouTube Music."

    return None


_MUSIC_SERVICES = {
    "spotify": {
        "display_name": "Spotify",
        "app_lookup_name": "spotify",
        # Spotify registers this URI scheme on install - opens the desktop
        # app to search results. Only used as a fallback when
        # _launch_resolved_music() can't resolve to one specific track.
        "search_uri": "spotify:search:{query}",
        "web_search_url": "https://open.spotify.com/search/{query}",
        "web_home_url": "https://open.spotify.com",
    },
    "youtube music": {
        "display_name": "YouTube Music",
        "app_lookup_name": "youtube music",
        # No registered URI scheme for the unofficial YouTube Music desktop
        # clients, so search always happens in-browser. Fallback only.
        "search_uri": None,
        "web_search_url": "https://music.youtube.com/search?q={query}",
        "web_home_url": "https://music.youtube.com",
    },
}


_MUSIC_SERVICE_ALIASES = {
    "spotify": "spotify",
    "youtube music": "youtube music", "youtube": "youtube music",
    "yt music": "youtube music", "ytmusic": "youtube music", "ytm": "youtube music",
}


def play_music(query: str = "", service: str = "spotify") -> str:
    """Plays music via Spotify (default) or YouTube Music. `query` is a
    song/artist/album/playlist to search for - leave blank to just open or
    resume whatever's already cued up. If the relevant API credentials are
    configured (config.SPOTIFY_CLIENT_ID/SECRET or config.YOUTUBE_API_KEY),
    resolves `query` to one specific track/video/album/playlist and
    actually starts it playing; otherwise just opens a search results page
    for the user to pick from. Tries the desktop app first if installed,
    otherwise falls back to opening the service in the browser - same
    approach as open_app(), just service-specific."""
    service_key = _MUSIC_SERVICE_ALIASES.get(service.strip().lower(), "spotify")
    info = _MUSIC_SERVICES[service_key]
    query = query.strip()

    action_desc = f"play '{query}' on {info['display_name']}" if query else f"open {info['display_name']}"
    if not _confirm(action_desc):
        return "Cancelled by user."

    app_path = _resolve_app_path(info["app_lookup_name"])

    # Try to resolve the query to one specific track/video and start it
    # playing, rather than opening a search page - see _launch_resolved_music.
    if query:
        resolved_reply = _launch_resolved_music(service_key, query, app_path)
        if resolved_reply:
            return resolved_reply

    can_deep_link_search = bool(query and info["search_uri"])

    if app_path and (not query or can_deep_link_search):
        try:
            if can_deep_link_search:
                os.startfile(info["search_uri"].format(query=urllib.parse.quote(query)))
                return (
                    f"Opened {info['display_name']} and searched for "
                    f"'{query}' - pick the track and I've got play/pause, "
                    "skip, and volume from there."
                )
            os.startfile(app_path)
            time.sleep(1.5)  # give it a moment to come to the foreground
            pyautogui.press("playpause")  # best-effort: resume whatever's cued up
            return f"Opened {info['display_name']}."
        except OSError as e:
            print(f"[play_music] app launch failed ({e}); falling back to browser")

    url = (
        info["web_search_url"].format(query=urllib.parse.quote(query))
        if query else info["web_home_url"]
    )
    webbrowser.open(url)
    if query:
        return (
            f"Opened '{query}' search results for {info['display_name']} "
            "in your browser - pick the track and I've got play/pause, "
            "skip, and volume from there."
        )
    return f"Opened {info['display_name']} in your browser."
