# Chrome Tab Group Recovery

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Made with Python](https://img.shields.io/badge/made%20with-Python-3776AB?logo=python&logoColor=white)](https://www.python.org)
[![Chrome](https://img.shields.io/badge/Chrome-SNSS-4285F4?logo=googlechrome&logoColor=white)](chrome_tab_group_browser.html)

A Python tool that decodes Google Chrome's binary SNSS session files to recover lost or deleted tab groups, including group names, colors, collapsed state, and all member tab URLs with page titles.

## The Problem

Chrome stores tab groups in binary session files using an undocumented format called SNSS (Session Service). When you accidentally close a window, lose tab groups during a crash, or Chrome updates reset your session, there is no built-in way to recover those groups. The "Recently Closed" menu only goes back so far, and Chrome's session restore is all-or-nothing.

This tool reads the raw binary files directly, decodes the SNSS commands, and reconstructs your tab groups with full URL and title data.

## Features

- Scans **all Chrome profiles** automatically (personal, work, etc.)
- Recovers tab group **names, colors, and collapsed state**
- Extracts **full URLs and page titles** for every tab in each group
- Reads **multiple session snapshots** per profile (Chrome keeps several)
- Deduplicates groups across session files, keeping the most complete version
- **Search** for specific groups by name
- **JSON output** for programmatic use
- **Interactive browser UI** for browsing groups and exporting URLs
- **Zero dependencies** -- uses only Python standard library

## Requirements

- Python 3.7+
- macOS (see [Platform Support](#platform-support) for Linux/Windows)
- Google Chrome (any recent version)

## Installation

```bash
git clone https://github.com/holzerjm/ChromeGroupTabRecovery.git
cd ChromeGroupTabRecovery
```

No `pip install` needed. The tool uses only Python builtins.

## Usage

### Scan all profiles

```bash
python3 chrome_tab_group_recovery.py
```

This scans every Chrome profile on your system, finds all session files, decodes every tab group, and saves the results to `recovered_tab_groups_all.txt`.

### Search for a specific group

```bash
python3 chrome_tab_group_recovery.py --search "MyProject"
```

Case-insensitive partial match. Only groups whose name contains the search term are shown.

### Scan a single profile

```bash
python3 chrome_tab_group_recovery.py --profile "Profile 1"
```

Use `--list-profiles` to see available profile directory names.

### List all profiles

```bash
python3 chrome_tab_group_recovery.py --list-profiles
```

Shows each profile's directory name, display name, email, and whether it has session data.

### JSON output

```bash
python3 chrome_tab_group_recovery.py --json
```

Outputs structured JSON with one object per group, suitable for piping to `jq` or loading into other tools.

### Custom output file

```bash
python3 chrome_tab_group_recovery.py -o my_recovery.txt
python3 chrome_tab_group_recovery.py --json -o my_recovery.json
```

### Combine options

```bash
python3 chrome_tab_group_recovery.py --profile "Default" --search "work" --json -o work_tabs.json
```

## Interactive Browser UI

The tool includes a browser-based UI for visually browsing your recovered tab groups and exporting selected URLs.

### Quick launch

```bash
python3 chrome_tab_group_recovery.py --ui
```

This scans all profiles, embeds the results into the HTML viewer, and opens it in your default browser automatically.

### Manual usage

You can also open `chrome_tab_group_browser.html` directly in any browser and load a JSON file:

1. Generate JSON: `python3 chrome_tab_group_recovery.py --json -o data.json`
2. Open `chrome_tab_group_browser.html` in your browser
3. Click **Load JSON** or drag-and-drop the JSON file onto the page

### UI Features

- **Profile/Group/Tab tree** -- collapsible hierarchy with color-coded group badges
- **Checkboxes** -- select entire groups (checks all member tabs) or individual tabs
- **Search filter** -- real-time filter across group names, tab titles, and URLs
- **Export panel** -- shows selected URLs in plain text, one per line
- **Copy to Clipboard** -- copies selected URLs, ready to paste into Bulk URL Opener or similar tools
- **Download .txt** -- saves selected URLs as a text file
- **Select All / Deselect All** -- bulk selection controls
- **Dark mode** -- automatically follows your system preference

### Workflow: Restoring Tab Groups with Bulk URL Opener

1. Run `python3 chrome_tab_group_recovery.py --ui`
2. Find the group you want to restore and check its checkbox
3. Click **Copy to Clipboard**
4. Open the [Bulk URL Opener](https://chromewebstore.google.com/detail/bulk-url-opener/kgnfciolbjojfdgnkjlklkmelpakgnii) extension in Chrome
5. Paste the URLs and click Open

## Example Output

```
================================================================================
CHROME TAB GROUP RECOVERY - ALL PROFILES
Generated: 2026-04-09 18:42:45
================================================================================

================================================================================
PROFILE: "Personal"
  Email:     user@gmail.com
  Directory: Default
================================================================================

  ────────────────────────────────────────────────────────────────────
  TAB GROUP: "Research"
    Color: blue  |  Collapsed: False  |  Tabs: 5
    UUID: 4168ac4c-d181-49ae-bc0e-ffeb1e0dda2f
    Source: Session_13419784054201841 (modified 2026-04-04 09:59)
  ────────────────────────────────────────────────────────────────────
    * How Transformers Work - A Detailed Exploration
      https://example.com/transformers-explained
    * Attention Is All You Need (arXiv)
      https://arxiv.org/abs/1706.03762
    * https://github.com/karpathy/nanoGPT
    ...
```

## CLI Reference

```
usage: chrome_tab_group_recovery.py [-h] [--profile PROFILE] [--search SEARCH]
                                     [--output OUTPUT] [--json] [--ui]
                                     [--list-profiles]

Recover Chrome tab groups from SNSS session files

options:
  -h, --help            show this help message and exit
  --profile PROFILE     Scan only this profile directory (e.g. 'Profile 1')
  --search SEARCH, -s SEARCH
                        Filter results to groups matching this name (case-insensitive)
  --output OUTPUT, -o OUTPUT
                        Output file path (default: stdout + auto-save)
  --json                Output as JSON instead of text
  --ui                  Launch interactive browser UI with recovery data
  --list-profiles       Just list available profiles and exit
```

## How It Works

### Chrome's Session Storage

Chrome persists session state in two types of binary files inside each profile's `Sessions/` directory:

| File Pattern | Contents |
|---|---|
| `Session_<timestamp>` | Window layout, tab ordering, tab group definitions, and tab navigation data |
| `Tabs_<timestamp>` | Per-tab navigation history (back/forward stack) |

The timestamp is a Chrome timestamp (microseconds since 1601-01-01 UTC). Chrome typically keeps 2-3 of each file, rotating them as the session changes.

### SNSS Binary Format

Each file starts with an 8-byte header:

```
[4 bytes] Magic: "SNSS"
[4 bytes] Version: uint32 (typically 3)
```

Followed by a stream of commands:

```
[2 bytes] Payload size (uint16). If 0xFFFF, next 4 bytes are the real size (uint32).
[1 byte]  Command ID
[N bytes] Command-specific payload
```

### Key Command Types

| Cmd ID | Name | Description |
|--------|------|-------------|
| 0 | SetTabWindow | Assigns a tab to a window (maps session tab ID to window ID) |
| 2 | SetTabIndexInWindow | Sets a tab's position index within its window |
| 6 | UpdateTabNavigation | Stores a navigation entry: URL, title, and session tab ID |
| 25 | SetTabGroup | Assigns a tab to a group via a 16-byte group token |
| 27 | SetTabGroupMetadata2 | Defines a group's name, color, collapsed state, and UUID |

### Command 27 Layout (SetTabGroupMetadata2)

```
Offset  Size     Field
──────  ───────  ─────────────────────────────
0       4        Total payload size (uint32)
4       16       Group token (binary, used to link tabs to this group)
20      4        Title length in UTF-16 characters (uint32)
24      N*2      Title string (UTF-16LE encoded)
24+N*2  4        Color ID (uint32): 0=grey, 1=blue, 2=red, 3=yellow,
                 4=green, 5=pink, 6=purple, 7=cyan, 8=orange
28+N*2  4        Collapsed flag (uint32, 0 or 1)
32+N*2  4        Is custom title flag (uint32)
36+N*2  4        Padding / saved_guid size
40+N*2  36       UUID as ASCII string (e.g. "4168ac4c-d181-49ae-...")
```

### Command 25 Layout (SetTabGroup)

```
Offset  Size     Field
──────  ───────  ─────────────────────────────
0       4        Session tab ID (uint32)
4       4        Padding (always 0)
8       16       Group token (matches token from cmd 27)
24      4        Has group flag (uint32, 1 = assigned, 0 = removed)
28      4        Padding
```

### Command 6 Layout (UpdateTabNavigation)

```
Offset  Size     Field
──────  ───────  ─────────────────────────────
0       4        Navigation tab ID (uint32)
4       4        Session tab ID (uint32, links to cmd 25)
8       4        Navigation index (uint32, position in back/forward stack)
12      4        URL byte length (uint32)
16      N        URL (Latin-1 encoded)
16+N    ...      Page title (UTF-16LE, preceded by uint16 length)
```

### Recovery Flow

1. **Discover profiles** by reading `Chrome/Local State` JSON and scanning for `Sessions/` directories
2. **Parse each Session file** into a stream of typed commands
3. **Extract group metadata** from cmd 27 (name, color, token, UUID)
4. **Build tab-to-group mapping** from cmd 25 (session tab ID to group token)
5. **Extract tab URLs** from cmd 6 (session tab ID to URL + title)
6. **Cross-reference** tab IDs to assemble the final group-to-URLs mapping
7. **Deduplicate** across session files, keeping the version with the most tabs per group

## Platform Support

### macOS (fully supported)

Chrome data is at:
```
~/Library/Application Support/Google/Chrome/
```

### Linux

Chrome data is typically at:
```
~/.config/google-chrome/
```

To use this tool on Linux, modify the `CHROME_DIR` variable at the top of the script:

```python
CHROME_DIR = Path.home() / ".config" / "google-chrome"
```

### Windows

Chrome data is typically at:
```
%LOCALAPPDATA%\Google\Chrome\User Data\
```

Modify the `CHROME_DIR` variable:

```python
CHROME_DIR = Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data"
```

### Chromium / Brave / Edge

Other Chromium-based browsers use the same SNSS format. Adjust `CHROME_DIR` to point to the browser's data directory:

- **Chromium (macOS):** `~/Library/Application Support/Chromium/`
- **Brave (macOS):** `~/Library/Application Support/BraveSoftware/Brave-Browser/`
- **Edge (macOS):** `~/Library/Application Support/Microsoft Edge/`

## Tips for Restoring Tabs

Once you have the recovery output:

1. **Open specific URLs**: Copy-paste URLs from the report directly into Chrome
2. **Bulk open**: Use a browser extension like "Open Multiple URLs" to paste a list of URLs and open them all at once
3. **Re-create groups manually**: After opening the tabs, select them and right-click to "Add tabs to group"
4. **Use the JSON output** with a script to automate opening tabs via Chrome's `--new-window` flag or the Chrome DevTools Protocol

## License

MIT License. See [LICENSE](LICENSE) for details.
