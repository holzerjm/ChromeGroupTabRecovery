#!/usr/bin/env python3
"""
Chrome Tab Group Recovery Tool
Decodes SNSS binary session files across all Chrome profiles
to recover tab group names, colors, and member tab URLs.

Usage:
    python3 chrome_tab_group_recovery.py [--profile PROFILE_DIR] [--search GROUP_NAME]

Examples:
    python3 chrome_tab_group_recovery.py                     # Scan all profiles
    python3 chrome_tab_group_recovery.py --search "RHS26"    # Search for specific group
    python3 chrome_tab_group_recovery.py --profile "Profile 1"  # Scan one profile
"""

import struct
import os
import re
import json
import sys
import argparse
import tempfile
import webbrowser
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

CHROME_DIR = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"

COLOR_NAMES = {
    0: "grey", 1: "blue", 2: "red", 3: "yellow", 4: "green",
    5: "pink", 6: "purple", 7: "cyan", 8: "orange"
}

# Chrome epoch: Jan 1, 1601
CHROME_EPOCH = datetime(1601, 1, 1)


def chrome_ts_to_datetime(ts):
    """Convert Chrome timestamp (microseconds since 1601-01-01) to datetime."""
    try:
        return CHROME_EPOCH + timedelta(microseconds=ts)
    except (OverflowError, OSError):
        return None


def ts_from_filename(fname):
    """Extract and convert timestamp from Session_/Tabs_ filename."""
    parts = fname.split("_")
    if len(parts) == 2 and parts[1].isdigit():
        return chrome_ts_to_datetime(int(parts[1]))
    return None


def get_profiles():
    """Discover all Chrome profiles with their display names and emails."""
    profiles = {}
    local_state = CHROME_DIR / "Local State"
    if local_state.exists():
        with open(local_state) as f:
            data = json.load(f)
        info_cache = data.get("profile", {}).get("info_cache", {})
        for pdir, info in info_cache.items():
            profiles[pdir] = {
                "name": info.get("name", pdir),
                "email": info.get("user_name", ""),
                "gaia_name": info.get("gaia_name", ""),
            }

    # Also check for directories that might not be in Local State (e.g. backups)
    if CHROME_DIR.exists():
        for d in CHROME_DIR.iterdir():
            if d.is_dir() and (d / "Sessions").is_dir():
                dirname = d.name
                if dirname not in profiles:
                    profiles[dirname] = {
                        "name": dirname,
                        "email": "",
                        "gaia_name": "",
                    }
    return profiles


def parse_snss(filepath):
    """Parse an SNSS file and return list of (cmd_id, payload) tuples."""
    commands = []
    try:
        with open(filepath, "rb") as f:
            magic = f.read(4)
            if magic != b"SNSS":
                return []
            f.read(4)  # version

            while True:
                size_data = f.read(2)
                if len(size_data) < 2:
                    break
                size = struct.unpack("<H", size_data)[0]
                if size == 0:
                    break
                if size == 0xFFFF:
                    size_data = f.read(4)
                    if len(size_data) < 4:
                        break
                    size = struct.unpack("<I", size_data)[0]
                payload = f.read(size)
                if len(payload) < size:
                    break
                if payload:
                    commands.append((payload[0], payload[1:]))
    except (IOError, OSError) as e:
        print(f"  Warning: Could not read {filepath}: {e}", file=sys.stderr)
    return commands


def decode_group_metadata(data):
    """
    Decode command 27 (SetTabGroupMetadata2).
    Layout:
      [0:4]   total_size (uint32)
      [4:20]  group_token (16 bytes)
      [20:22] title_length (uint16, in UTF-16 chars)
      [22:22+title_length*2] title (UTF-16LE)
      then:   color (uint32), collapsed (uint32), is_custom_title (uint32)
      then:   4 bytes padding + UUID as ASCII string
    """
    if len(data) < 28:
        return None

    token = data[4:20]
    # Title length is uint32 at offset 20
    title_len = struct.unpack("<I", data[20:24])[0]
    title = ""
    if title_len > 0 and 24 + title_len * 2 <= len(data):
        try:
            title = data[24 : 24 + title_len * 2].decode("utf-16-le")
        except UnicodeDecodeError:
            pass

    after_title = 24 + title_len * 2
    color_id = -1
    collapsed = False
    if after_title + 12 <= len(data):
        color_id = struct.unpack("<I", data[after_title : after_title + 4])[0]
        collapsed = struct.unpack("<I", data[after_title + 4 : after_title + 8])[0] != 0

    # Extract UUID string near the end
    ascii_data = data.decode("latin-1")
    uuid_match = re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", ascii_data
    )

    return {
        "token": token,
        "title": title.strip(),
        "color": COLOR_NAMES.get(color_id, f"id={color_id}"),
        "color_id": color_id,
        "collapsed": collapsed,
        "uuid": uuid_match.group(0) if uuid_match else "",
    }


def extract_session_data(session_path):
    """
    Extract tab groups, tab-to-group mappings, and tab URLs from a Session file.
    Returns (groups_dict, tab_urls_dict).
    """
    commands = parse_snss(session_path)
    if not commands:
        return {}, {}

    # --- Tab group metadata (cmd 27) ---
    groups = {}  # token_hex -> group_info
    for cmd_id, data in commands:
        if cmd_id == 27:
            meta = decode_group_metadata(data)
            if meta:
                token_hex = meta["token"].hex()
                groups[token_hex] = {
                    "title": meta["title"],
                    "color": meta["color"],
                    "collapsed": meta["collapsed"],
                    "uuid": meta["uuid"],
                    "tab_ids": [],
                }

    # --- Tab-to-group assignments (cmd 25) ---
    for cmd_id, data in commands:
        if cmd_id == 25 and len(data) >= 28:
            tab_id = struct.unpack("<I", data[0:4])[0]
            token_hex = data[8:24].hex()
            has_group = struct.unpack("<I", data[24:28])[0]
            if has_group and token_hex in groups:
                groups[token_hex]["tab_ids"].append(tab_id)

    # --- Tab navigation URLs (cmd 6) ---
    # Format: [nav_tab_id:4][session_tab_id:4][nav_index:4][url_len:4][url][title...]
    tab_urls = {}  # session_tab_id -> {url, title, nav_index}
    for cmd_id, data in commands:
        if cmd_id == 6 and len(data) >= 16:
            session_tab_id = struct.unpack("<I", data[4:8])[0]
            nav_index = struct.unpack("<I", data[8:12])[0]
            url_len = struct.unpack("<I", data[12:16])[0]

            if url_len > 0 and 16 + url_len <= len(data):
                url = data[16 : 16 + url_len].decode("latin-1", errors="replace")

                # Extract page title (UTF-16LE) after the URL
                title = ""
                remaining = data[16 + url_len :]
                for i in range(0, min(len(remaining) - 4, 80)):
                    if i + 2 > len(remaining):
                        break
                    slen = struct.unpack("<H", remaining[i : i + 2])[0]
                    if 2 <= slen <= 300 and i + 2 + slen * 2 <= len(remaining):
                        try:
                            candidate = remaining[i + 2 : i + 2 + slen * 2].decode(
                                "utf-16-le"
                            )
                            if (
                                candidate.isprintable()
                                and candidate.strip()
                                and not candidate.startswith("http")
                            ):
                                title = candidate.strip()
                                break
                        except UnicodeDecodeError:
                            pass

                # Keep the latest navigation entry per tab
                if session_tab_id not in tab_urls or nav_index >= tab_urls[
                    session_tab_id
                ].get("nav_index", -1):
                    tab_urls[session_tab_id] = {
                        "url": url,
                        "title": title,
                        "nav_index": nav_index,
                    }

    return groups, tab_urls


def scan_profile(profile_dir, profile_info):
    """Scan a single profile's Sessions directory and return all group data."""
    sessions_dir = CHROME_DIR / profile_dir / "Sessions"
    if not sessions_dir.exists():
        return []

    results = []
    session_files = sorted(
        [f for f in sessions_dir.iterdir() if f.name.startswith("Session_")],
        key=lambda f: f.stat().st_size,
        reverse=True,  # largest first (most complete)
    )

    for sf in session_files:
        file_date = ts_from_filename(sf.name)
        file_mod = datetime.fromtimestamp(sf.stat().st_mtime)
        groups, tab_urls = extract_session_data(sf)

        if not groups:
            continue

        # Enrich groups with URL data
        for token_hex, group in groups.items():
            tabs_with_urls = []
            for tid in sorted(group["tab_ids"]):
                if tid in tab_urls:
                    info = tab_urls[tid]
                    if "chrome://saved-tab-groups-unsupported" in info["url"]:
                        continue
                    tabs_with_urls.append(
                        {"url": info["url"], "title": info["title"], "tab_id": tid}
                    )
            group["tabs"] = tabs_with_urls

        results.append(
            {
                "file": sf.name,
                "file_size": sf.stat().st_size,
                "file_modified": file_mod.strftime("%Y-%m-%d %H:%M"),
                "file_date": file_date.strftime("%Y-%m-%d %H:%M") if file_date else "?",
                "groups": groups,
                "total_nav_entries": len(tab_urls),
            }
        )

    return results


def format_report(all_data, search=None):
    """Format the full report as text."""
    lines = []
    lines.append("=" * 80)
    lines.append("CHROME TAB GROUP RECOVERY - ALL PROFILES")
    lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if search:
        lines.append(f"Search filter: '{search}'")
    lines.append("=" * 80)

    total_groups = 0
    total_tabs = 0

    for profile_dir, profile_info, results in all_data:
        if not results:
            continue

        # Check if any groups match search filter
        if search:
            has_match = False
            for r in results:
                for g in r["groups"].values():
                    if search.lower() in g["title"].lower():
                        has_match = True
                        break
            if not has_match:
                continue

        lines.append("")
        lines.append("=" * 80)
        lines.append(f'PROFILE: "{profile_info["name"]}"')
        lines.append(f'  Email:     {profile_info["email"]}')
        lines.append(f"  Directory: {profile_dir}")
        lines.append("=" * 80)

        # Deduplicate groups across session files by UUID
        # Use the version with the most tabs
        best_groups = {}  # uuid -> (group_data, session_file_info)
        for r in results:
            for token_hex, group in r["groups"].items():
                uuid = group["uuid"]
                key = uuid if uuid else token_hex
                existing = best_groups.get(key)
                if not existing or len(group["tabs"]) > len(existing[0]["tabs"]):
                    best_groups[key] = (group, r)

        for key, (group, r) in sorted(
            best_groups.items(), key=lambda x: x[1][0]["title"]
        ):
            title = group["title"] or "(untitled)"

            if search and search.lower() not in title.lower():
                continue

            total_groups += 1
            tab_count = len(group["tabs"])
            total_tabs += tab_count

            lines.append("")
            lines.append(f"  {'─' * 68}")
            lines.append(f'  TAB GROUP: "{title}"')
            lines.append(
                f"    Color: {group['color']}  |  "
                f"Collapsed: {group['collapsed']}  |  "
                f"Tabs: {tab_count}"
            )
            lines.append(f"    UUID: {group['uuid']}")
            lines.append(
                f"    Source: {r['file']} (modified {r['file_modified']})"
            )
            lines.append(f"  {'─' * 68}")

            for tab in group["tabs"]:
                if tab["title"]:
                    lines.append(f"    * {tab['title']}")
                    lines.append(f"      {tab['url']}")
                else:
                    lines.append(f"    * {tab['url']}")

            if not group["tabs"] and group["tab_ids"]:
                lines.append(
                    f"    ({len(group['tab_ids'])} tabs assigned but URLs not "
                    f"found in this session file)"
                )

    lines.append("")
    lines.append("=" * 80)
    lines.append(f"SUMMARY: {total_groups} groups, {total_tabs} tabs with URLs")
    lines.append("=" * 80)

    return "\n".join(lines)


def build_json_data(all_data):
    """Convert scan results into the JSON structure used by --json and --ui."""
    json_data = []
    for profile_dir, profile_info, results in all_data:
        for r in results:
            for token_hex, group in r["groups"].items():
                json_data.append(
                    {
                        "profile": profile_dir,
                        "profile_name": profile_info["name"],
                        "profile_email": profile_info["email"],
                        "session_file": r["file"],
                        "group_title": group["title"],
                        "group_color": group["color"],
                        "group_uuid": group["uuid"],
                        "group_collapsed": group["collapsed"],
                        "tab_count": len(group["tabs"]),
                        "tabs": [
                            {"url": t["url"], "title": t["title"]}
                            for t in group["tabs"]
                        ],
                    }
                )
    return json_data


def launch_ui(json_data):
    """Generate a self-contained HTML file with embedded data and open it."""
    # Try to find the companion HTML template
    html_template = Path(__file__).parent / "chrome_tab_group_browser.html"
    if html_template.exists():
        html_content = html_template.read_text(encoding="utf-8")
    else:
        print("Error: chrome_tab_group_browser.html not found.", file=sys.stderr)
        print("Place it next to this script.", file=sys.stderr)
        sys.exit(1)

    # Inject the data as a JS variable before the closing </script> tag
    data_script = (
        f"<script>var EMBEDDED_DATA = {json.dumps(json_data)};</script>\n"
    )
    # Insert before the main <script> block
    html_content = html_content.replace(
        "<script>",
        data_script + "<script>",
        1,
    )

    # Write to a temp file and open it
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", prefix="chrome_tab_groups_", delete=False
    ) as f:
        f.write(html_content)
        tmp_path = f.name

    url = "file://" + tmp_path
    print(f"Opening browser UI: {url}", file=sys.stderr)
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(
        description="Recover Chrome tab groups from SNSS session files"
    )
    parser.add_argument(
        "--profile",
        help="Scan only this profile directory (e.g. 'Profile 1')",
    )
    parser.add_argument(
        "--search", "-s",
        help="Filter results to groups matching this name (case-insensitive)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output file path (default: stdout + auto-save)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of text",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Launch interactive browser UI with recovery data",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="Just list available profiles and exit",
    )
    args = parser.parse_args()

    profiles = get_profiles()

    if args.list_profiles:
        print(f"{'Directory':25s} {'Name':30s} {'Email'}")
        print("-" * 80)
        for pdir, info in sorted(profiles.items()):
            sessions_dir = CHROME_DIR / pdir / "Sessions"
            has_sessions = "Y" if sessions_dir.exists() else "N"
            print(
                f"{pdir:25s} {info['name']:30s} {info['email']:40s} "
                f"Sessions: {has_sessions}"
            )
        return

    # Determine which profiles to scan
    if args.profile:
        scan_profiles = {
            args.profile: profiles.get(
                args.profile, {"name": args.profile, "email": "", "gaia_name": ""}
            )
        }
    else:
        scan_profiles = profiles

    # Scan all profiles
    all_data = []
    for profile_dir, profile_info in sorted(scan_profiles.items()):
        label = f'{profile_info["name"]} ({profile_info["email"]})' if profile_info["email"] else profile_info["name"]
        print(f"Scanning: {profile_dir} - {label} ...", file=sys.stderr)
        results = scan_profile(profile_dir, profile_info)
        all_data.append((profile_dir, profile_info, results))

        # Quick summary to stderr
        for r in results:
            group_names = [
                g["title"] or "(untitled)" for g in r["groups"].values()
            ]
            if group_names:
                print(
                    f"  {r['file']}: {len(group_names)} groups: "
                    f"{', '.join(repr(n) for n in group_names)}",
                    file=sys.stderr,
                )

    # Build structured JSON data from scan results
    json_data = build_json_data(all_data)

    # --ui mode: launch browser
    if args.ui:
        launch_ui(json_data)
        return

    if args.json:
        output = json.dumps(json_data, indent=2)
    else:
        output = format_report(all_data, search=args.search)

    # Output
    if args.output:
        outpath = Path(args.output)
    else:
        outpath = Path(__file__).parent / "recovered_tab_groups_all.txt"
        if args.json:
            outpath = outpath.with_suffix(".json")

    with open(outpath, "w") as f:
        f.write(output)
    print(f"\nSaved to: {outpath}", file=sys.stderr)

    # Also print to stdout
    print(output)


if __name__ == "__main__":
    main()
