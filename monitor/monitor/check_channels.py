
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------
# Configuration
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

CHANNELS_FILE = BASE_DIR / "monitor" / "channels.json"
STATE_FILE = BASE_DIR / "monitor" / "state.json"
OUTPUT_FILE = BASE_DIR / "data" / "new-episodes.json"

API_BASE = "https://www.googleapis.com/youtube/v3"

MAX_VIDEOS_PER_CHANNEL = 50
MAX_STORED_VIDEO_IDS = 200


# --------------------------------------------------
# File handling
# --------------------------------------------------

def load_json(path, default):
    if not path.exists():
        return default

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


# --------------------------------------------------
# YouTube API
# --------------------------------------------------

def youtube_request(endpoint, params, api_key):
    params["key"] = api_key

    url = (
        f"{API_BASE}/{endpoint}?"
        f"{urllib.parse.urlencode(params)}"
    )

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"}
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_upload_playlists(channels, api_key):
    """Retrieve uploads playlists in batches of up to 50 channels."""

    playlists = {}

    channel_ids = [channel["id"] for channel in channels]

    for start in range(0, len(channel_ids), 50):
        batch = channel_ids[start:start + 50]

        result = youtube_request(
            "channels",
            {
                "part": "contentDetails",
                "id": ",".join(batch),
                "maxResults": 50
            },
            api_key
        )

        for item in result.get("items", []):
            channel_id = item["id"]

            uploads_id = (
                item["contentDetails"]
                ["relatedPlaylists"]
                ["uploads"]
            )

            playlists[channel_id] = uploads_id

    return playlists


def get_recent_uploads(playlist_id, api_key):
    """Retrieve the most recent uploads from a channel."""

    result = youtube_request(
        "playlistItems",
        {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": MAX_VIDEOS_PER_CHANNEL
        },
        api_key
    )

    uploads = []

    for item in result.get("items", []):
        snippet = item.get("snippet", {})
        resource = snippet.get("resourceId", {})

        video_id = resource.get("videoId")

        if not video_id:
            continue

        uploads.append({
            "videoId": video_id,
            "title": snippet.get("title", "Untitled video"),
            "publishedAt": snippet.get("publishedAt"),
            "url": (
                f"https://www.youtube.com/watch?v={video_id}"
            )
        })

    return uploads


# --------------------------------------------------
# Main monitoring process
# --------------------------------------------------

def main():

    api_key = os.environ.get("YOUTUBE_API_KEY")

    if not api_key:
        raise RuntimeError(
            "YOUTUBE_API_KEY environment variable is missing"
        )

    config = load_json(
        CHANNELS_FILE,
        {"channels": []}
    )

    channels = config.get("channels", [])

    if not channels:
        raise RuntimeError(
            "No channels found in monitor/channels.json"
        )

    # Validate configuration
    seen_channel_ids = set()

    for channel in channels:

        if not channel.get("id") or not channel.get("name"):
            raise RuntimeError(
                "Every channel must have both a name and an id"
            )

        if channel["id"] in seen_channel_ids:
            raise RuntimeError(
                f"Duplicate channel ID: {channel['id']}"
            )

        seen_channel_ids.add(channel["id"])

    state = load_json(
        STATE_FILE,
        {"channels": {}}
    )

    channel_state = state.setdefault("channels", {})

    playlists = get_upload_playlists(channels, api_key)

    new_episodes = []
    checked_channels = 0

    for channel in channels:

        channel_id = channel["id"]
        channel_name = channel["name"]

        playlist_id = playlists.get(channel_id)

        if not playlist_id:
            print(
                f"Warning: No uploads playlist found for "
                f"{channel_name}"
            )
            continue

        uploads = get_recent_uploads(
            playlist_id,
            api_key
        )

        previous = channel_state.get(channel_id)

        previous_ids = (
            set(previous.get("seen_ids", []))
            if previous else set()
        )

        # New channels establish a baseline first.
        # This prevents old uploads generating false alerts.

        if previous is None:

            print(
                f"Initial baseline recorded for {channel_name}"
            )

        else:

            for video in uploads:

                if video["videoId"] not in previous_ids:

                    new_episodes.append({
                        "channelName": channel_name,
                        "channelId": channel_id,
                        **video
                    })

        # Record recent uploads to avoid duplicate alerts.

        current_ids = [
            video["videoId"]
            for video in uploads
        ]

        combined_ids = list(
            dict.fromkeys(
                current_ids + list(previous_ids)
            )
        )

        channel_state[channel_id] = {
            "seen_ids": combined_ids[:MAX_STORED_VIDEO_IDS],
            "last_checked": datetime.now(
                timezone.utc
            ).isoformat()
        }

        checked_channels += 1

        print(
            f"Checked {channel_name}: "
            f"{len(uploads)} recent uploads"
        )

    # --------------------------------------------------
    # Publish results
    # --------------------------------------------------

    output = {
        "generatedAt": datetime.now(
            timezone.utc
        ).isoformat(),
        "channelsChecked": checked_channels,
        "newEpisodes": new_episodes
    }

    save_json(STATE_FILE, state)
    save_json(OUTPUT_FILE, output)

    print("--------------------------------")
    print(f"Channels checked: {checked_channels}")
    print(f"New uploads detected: {len(new_episodes)}")
    print(f"Results written to: {OUTPUT_FILE}")
    print("--------------------------------")


if __name__ == "__main__":

    try:
        main()

    except Exception as error:

        print(
            f"MONITOR FAILED: {error}",
            file=sys.stderr
        )

        sys.exit(1)
