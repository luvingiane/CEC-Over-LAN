#!/usr/bin/env python3
"""Show the buttons of the TV remote instead of keyboard keys in Steam's Big Picture hints.

Steam's Big Picture draws its hints ("Esc BACK", "Enter SELECT", "F1 MENU") as `.KeyCap` elements
that contain the key name. The remote sends those keys through the receiver, so this program
works out, from the key mapping, which button of the remote produces each key and replaces the
key cap with that button: where the mapping says Back sends Esc, "Esc BACK" becomes the remote's
back arrow. Nothing in Steam's files is changed: a script is injected into the running UI through
the DevTools port (Steam must be started with `-cef-enable-debugging`).

  steam_glyphs.py --once     inject once
  steam_glyphs.py --watch    keep it injected (re-inject after a Steam restart or a mapping change)
  steam_glyphs.py --remove   put the keyboard keys back
  steam_glyphs.py --dump     print the key caps currently shown (to learn Steam's labels)
  steam_glyphs.py --map      print the computed map and exit
"""
import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import cdp  # noqa: E402
import tv_remote as core  # noqa: E402

# While Steam has the focus, per-app exceptions whose text is in here apply.
FOCUS_TEXT = "steam steam big picture mode steamwebhelper"

# injected key -> the texts Steam may print on its key cap. Steam translates the names of some keys
# (on the owner's machine, in Italian, Enter is "Invio"); `--dump` prints what Steam really shows,
# which is how a missing name is found. The Italian ones other than Invio are not verified.
STEAM_LABEL = {"KEY_ESC": ("Esc",), "KEY_ENTER": ("Enter", "Invio"), "KEY_SPACE": ("Space", "Spazio"),
               "KEY_TAB": ("Tab",), "KEY_BACKSPACE": ("Backspace",), "KEY_DELETE": ("Del", "Canc"),
               "KEY_INSERT": ("Ins",), "KEY_HOME": ("Home",), "KEY_END": ("End", "Fine"),
               "KEY_PAGEUP": ("PgUp",), "KEY_PAGEDOWN": ("PgDn",),
               "KEY_UP": ("\u2191",), "KEY_DOWN": ("\u2193",), "KEY_LEFT": ("\u2190",), "KEY_RIGHT": ("\u2192",)}
STEAM_LABEL.update({f"KEY_F{i}": (f"F{i}",) for i in range(1, 13)})
STEAM_LABEL.update({f"KEY_{c}": (c,) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"})

# how a button is drawn when the remote model does not say (the built-in Xiaomi model does):
# 'round' icon, 'pill' text, 'col' colour
KIND = {**{b: "round" for b in ("back", "home", "settings", "power", "mic", "up", "down", "left", "right",
                                "volup", "voldown", "chup", "chdown", "ok")},
        **{f"n{i}": "round" for i in range(10)},
        **{b: "pill" for b in ("exit", "mute", "info", "ttx", "epg", "audio", "source", "netflix", "prime",
                               "youtube", "tvplus", "star", "livetv")},
        "red": "col", "green": "col", "yellow": "col", "blue": "col"}
# if two buttons produce the same key, the first of these wins
PRIORITY = ["ok", "back", "home", "up", "down", "left", "right", "exit", "red", "green", "yellow", "blue"]


def pick_entry(entry):
    """The action that applies while Steam has the focus (its app exception, if any)."""
    for pattern, override in entry.get("apps") or []:
        if core.match_form(pattern) in FOCUS_TEXT:
            return override
    return entry


def build_map(entries, buttons):
    """{Steam key-cap text: {label, kind, color, title}} from the mapping and the remote model.

    Only single-key tap actions are drawn: a long press or a combination has no key cap."""
    by_code = {b["code"]: (bid, b) for bid, b in buttons.items() if b.get("code")}
    order = {bid: i for i, bid in enumerate(PRIORITY)}
    result, rank = {}, {}
    for code, entry in entries.items():
        if code not in by_code:
            continue
        bid, button = by_code[code]
        tap = pick_entry(entry)["tap"]
        if not tap or tap["type"] != "keys" or len(tap["keys"]) != 1 or entry_has_long(pick_entry(entry)):
            continue
        caps = STEAM_LABEL.get(tap["keys"][0])
        if not caps:
            continue
        kind = button.get("kind") or KIND.get(bid, "pill")   # the model says how a button is drawn
        color = button.get("color") or (bid if kind == "col" else "")
        glyph = {"label": button["label"], "kind": kind,
                 "color": color if kind == "col" else "", "title": button.get("title", bid)}
        for cap in caps:
            if cap not in result or order.get(bid, 99) < rank[cap]:
                result[cap], rank[cap] = glyph, order.get(bid, 99)
    return result


def entry_has_long(entry):
    return bool(entry.get("long"))


JS = r"""
(() => {
  const MAP = __MAP__;
  if (window.__cecGlyphs) { window.__cecGlyphs.map = MAP; window.__cecGlyphs.apply(); return 'updated'; }
  const css = document.createElement('style'); css.id = 'cec-glyph-style';
  css.textContent = `
    .KeyCap.cec-glyph { border-radius: 999px !important; min-width: 2.1em; text-align: center; font-weight: 700 !important;
      background: #2a2f38 !important; color: #fff !important; border: 1px solid rgba(255,255,255,.45) !important; }
    .KeyCap.cec-glyph .Inner { color: #fff !important; }
    .KeyCap.cec-pill { background: #f2f4f8 !important; } .KeyCap.cec-pill .Inner { color: #1b1d21 !important; }
    .KeyCap.cec-col .Inner { color: #101216 !important; }
    .KeyCap.cec-red { background: #d9534f !important; } .KeyCap.cec-green { background: #3ba55d !important; }
    .KeyCap.cec-yellow { background: #e8c547 !important; } .KeyCap.cec-blue { background: #3b8eea !important; }`;
  document.head.appendChild(css);
  const G = { map: MAP, mine: new WeakMap() };
  G.apply = () => {
    document.querySelectorAll('.KeyCap').forEach(cap => {
      const inner = cap.querySelector('.Inner') || cap;
      const was = G.mine.get(cap);
      let key = inner.textContent.trim();
      if (was && key === was.shown) key = was.key;            // our own text: the original key
      const g = G.map[key];
      if (g) {
        if (inner.textContent !== g.label) inner.textContent = g.label;
        cap.className = cap.className.replace(/\bcec-\S+/g, '').trim() + ' cec-glyph cec-' + g.kind.replace(' ', '-') + (g.color ? ' cec-' + g.color : '');
        cap.title = g.title; G.mine.set(cap, { key, shown: g.label });
      } else if (was) {                                        // no longer mapped: give the key back
        inner.textContent = was.key; cap.className = cap.className.replace(/\bcec-\S+/g, '').trim(); G.mine.delete(cap);
      }
    });
  };
  G.remove = () => { G.map = {}; G.apply(); G.observer.disconnect(); css.remove(); delete window.__cecGlyphs; };
  G.keys = () => [...document.querySelectorAll('.KeyCap')].map(c => (c.querySelector('.Inner') || c).textContent.trim());
  G.observer = new MutationObserver(() => G.apply());
  G.observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  window.__cecGlyphs = G; G.apply(); return 'installed';
})()
"""


def load_map():
    """Compute the map from the owner's keymap.json and the remote model."""
    import tv_remote_web as web
    try:
        raw = json.loads(core.KEYMAP_FILE.read_text())["keys"]
    except (OSError, ValueError, KeyError):
        raw = core.default_keymap()
    entries, _ = core.normalise(raw)
    return build_map(entries, web.remote_model()["buttons"])


def bigpicture_pages():
    return [p for p in cdp.list_pages() if "big picture" in (p.get("title") or "").lower() and p.get("webSocketDebuggerUrl")]


def run_on_pages(expression):
    out = []
    for page in bigpicture_pages():
        c = cdp.CDP(page["webSocketDebuggerUrl"])
        try:
            out.append(c.evaluate(expression))
        finally:
            c.close()
    return out


def inject(mapping):
    return run_on_pages(JS.replace("__MAP__", json.dumps(mapping, ensure_ascii=False)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--remove", action="store_true")
    p.add_argument("--dump", action="store_true")
    p.add_argument("--map", action="store_true")
    p.add_argument("--interval", type=float, default=4.0)
    a = p.parse_args()
    if a.map:
        print(json.dumps(load_map(), indent=1, ensure_ascii=False))
    elif a.dump:
        print(run_on_pages("(window.__cecGlyphs ? window.__cecGlyphs.keys() : [...document.querySelectorAll('.KeyCap')].map(c => (c.querySelector('.Inner') || c).textContent.trim()))"))
    elif a.remove:
        print(run_on_pages("window.__cecGlyphs ? (window.__cecGlyphs.remove(), 'removed') : 'not installed'"))
    elif a.once:
        print(inject(load_map()))
    elif a.watch:
        last = None
        while True:
            try:
                mapping = load_map()
                present = run_on_pages("typeof window.__cecGlyphs")
                if mapping != last or (present and "undefined" in present):
                    print(f"[steam-glyphs] {inject(mapping)} ({len(mapping)} glyphs)", flush=True)
                    last = mapping
            except (OSError, ConnectionError, RuntimeError, ValueError) as exc:
                last = None
                print(f"[steam-glyphs] waiting for Steam ({type(exc).__name__})", flush=True)
            time.sleep(a.interval)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
