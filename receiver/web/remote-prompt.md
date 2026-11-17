You are helping me set up CEC-Over-LAN, a program that lets the remote of my TV control a computer. Its web page draws my remote on the phone screen, and you will write the description of that drawing.

STEP 1. Ask me for a photo of my remote and wait for it. Do not write anything else before you have it. The photo should be well lit, taken straight from above, with the whole remote in the frame and the labels readable. If anything on the remote is too small or blurry to read, ask me about that button instead of guessing.

STEP 2. Look at the photo and answer with ONE JSON object and nothing else: no introduction, no comments, no explanation after it. A single code block is fine. The format is below.

## Format

```
{
  "name": "Brand and model of the remote, as printed on it",
  "layout": [ ...rows, from the top of the remote to the bottom... ],
  "buttons": { "<id>": { ...one entry per physical button... } }
}
```

### buttons

One entry per button you can see, keyed by an id of lower-case letters, digits and `_` (at most 24 characters): `power`, `n1`, `volup`, `red`.

| field   | meaning |
|---------|---------|
| `label` | What is drawn on the button on screen: 1 to 12 characters. Use the text printed on the remote ("EXIT", "INFO", "OK") or a plain symbol (`▲ ▼ ◀ ▶ ← ⌂ + −`). Never an emoji. |
| `title` | The button's name in words, for the owner: "Volume +", "Netflix button". |
| `code`  | The Linux key name the button sends, for example `KEY_VOLUMEUP`, or `null`. See "Codes" below. |
| `guess` | `true` if you deduced the code and are not certain. Leave it out when `code` is `null`. |
| `shape` | `round` (a round key, the default), `small` (a small oval key), `wide` (a wide oval key, for streaming shortcuts) or `color` (a coloured round key). |
| `color` | Only with `"shape": "color"`: `red`, `green`, `yellow` or `blue`. |

### layout

A list of rows, from the top of the remote to the bottom, copying what the photo shows. The drawing is 320 px wide: a row holds at most 4 round keys, 3 small ones or 2 wide ones. About 12 to 14 rows is the practical maximum, so merge rows of similar keys. If a button has to be left out to fit, leave it out of both `layout` and `buttons`.

A row is one of:

* `{"items": ["id", "id", ...]}`: 1 to 6 slots side by side. Add `"align": "between"` to push two keys to the opposite edges (power on the left, microphone on the right).
* `{"dpad": {"up": "id", "down": "id", "left": "id", "right": "id", "ok": "id"}}`: the ring with the four arrows and the OK key in the middle. All five ids are required.

A slot is a button id, or:

* `{"rocker": ["id_top", "id_bottom"]}`: a tall two-part key, like volume + / − or channel ∧ / ∨.
* `{"stack": ["id_top", "id_bottom"]}`: two small keys one above the other.

Every id in the layout must exist in `buttons`, and appears once.

### Codes

The program reads the key names that the TV reports over adb (what `adb shell getevent -l` prints). On Android TV and Google TV remotes these are the common ones:

```
arrows      KEY_UP KEY_DOWN KEY_LEFT KEY_RIGHT        OK  KEY_SELECT
back        KEY_BACK       home  KEY_HOMEPAGE         menu KEY_MENU
volume      KEY_VOLUMEUP KEY_VOLUMEDOWN KEY_MUTE       power KEY_POWER
channel     KEY_CHANNELUP KEY_CHANNELDOWN             digits KEY_0 ... KEY_9
playback    KEY_PLAYPAUSE KEY_NEXTSONG KEY_PREVIOUSSONG KEY_FASTFORWARD KEY_REWIND
```

Rules:

* Put a code only where you are confident. Everything else is `null`: the owner presses the button on the real remote and the web page learns the code. A `null` is correct and useful; a wrong code is harmful.
* Do not copy the codes of another remote model. Colour keys, shortcut keys (Netflix, YouTube...), "Live TV", "Source", "Info" and "Assistant" vary a lot between makers: `null` unless you know this exact model.
* Two buttons never share a code.
* If you deduced a code from its function (digits, volume, arrows), add `"guess": true`.
* Never invent a button that is not in the photo, and never invent a label you cannot read.

## Example (a small, invented remote)

```
{
  "name": "Acme Streamer remote",
  "layout": [
    {"align": "between", "items": ["power", "mic"]},
    {"dpad": {"up": "up", "down": "down", "left": "left", "right": "right", "ok": "ok"}},
    {"items": ["back", "home", "menu"]},
    {"items": [{"rocker": ["volup", "voldown"]}, {"stack": ["info", "mute"]}]},
    {"items": ["netflix", "youtube"]},
    {"items": ["red", "green", "yellow", "blue"]}
  ],
  "buttons": {
    "power":   {"label": "⏻",  "title": "Power", "code": "KEY_POWER", "guess": true},
    "mic":     {"label": "MIC", "title": "Voice assistant", "code": null},
    "up":      {"label": "▲", "title": "Up", "code": "KEY_UP"},
    "down":    {"label": "▼", "title": "Down", "code": "KEY_DOWN"},
    "left":    {"label": "◀", "title": "Left", "code": "KEY_LEFT"},
    "right":   {"label": "▶", "title": "Right", "code": "KEY_RIGHT"},
    "ok":      {"label": "OK", "title": "OK", "code": "KEY_SELECT"},
    "back":    {"label": "←", "title": "Back", "code": "KEY_BACK"},
    "home":    {"label": "⌂", "title": "Home", "code": "KEY_HOMEPAGE"},
    "menu":    {"label": "≡", "title": "Menu", "code": null},
    "volup":   {"label": "+", "title": "Volume +", "code": "KEY_VOLUMEUP"},
    "voldown": {"label": "−", "title": "Volume −", "code": "KEY_VOLUMEDOWN"},
    "info":    {"label": "INFO", "title": "Info", "code": null, "shape": "small"},
    "mute":    {"label": "MUTE", "title": "Mute", "code": "KEY_MUTE", "shape": "small"},
    "netflix": {"label": "NETFLIX", "title": "Netflix button", "code": null, "shape": "wide"},
    "youtube": {"label": "YouTube", "title": "YouTube button", "code": null, "shape": "wide"},
    "red":     {"label": "R", "title": "Red", "code": null, "shape": "color", "color": "red"},
    "green":   {"label": "G", "title": "Green", "code": null, "shape": "color", "color": "green"},
    "yellow":  {"label": "Y", "title": "Yellow", "code": null, "shape": "color", "color": "yellow"},
    "blue":    {"label": "B", "title": "Blue", "code": null, "shape": "color", "color": "blue"}
  }
}
```

The example only shows the format. Describe the remote in MY photo, not this one.

Before you answer, check: the JSON is valid (no trailing commas, no comments); every id in `layout` is in `buttons`; no id is used twice in the layout; no two buttons share a code; `color` is present on every `color` button. Now ask me for the photo.
