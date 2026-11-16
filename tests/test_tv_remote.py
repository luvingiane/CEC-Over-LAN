"""Offline tests for the keymap and the key router (no TV, no uinput).

Run from the repository root:  python3 -m unittest discover -s tests -v
"""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "receiver"))
import tv_remote as core  # noqa: E402


class FakeOut:
    def __init__(self):
        self.events = []

    def key(self, name, value):
        self.events.append((name, value))


def make_router(raw=None):
    tmp = tempfile.mkdtemp()
    path = Path(tmp) / "keymap.json"
    if raw is not None:
        path.write_text(json.dumps({"version": 1, "keys": raw}))
    keymap = core.Keymap(path, "KEY_BACKSPACE")
    out = FakeOut()
    launches = []
    router = core.Router(keymap, out, launcher=launches.append)
    router.launches = launches
    return router, out, path


class Normalise(unittest.TestCase):
    def test_valid_and_invalid(self):
        entries, errors = core.normalise({
            "KEY_8": {"tap": "KEY_8"},
            "KEY_EJECTCD": {"long": "KEY_LEFTALT+KEY_F4", "hold": 3},
            "bad key": {"tap": "KEY_A"},
            "KEY_X": {"tap": "KEY_POWER"},          # denied target
            "KEY_Y": {"tap": "not a key"},
            "KEY_Z": {"tap": "KEY_A", "hold": 99},  # hold out of range
            "KEY_W": {},                            # no action
        })
        self.assertEqual(set(entries), {"KEY_8", "KEY_EJECTCD"})
        self.assertEqual(entries["KEY_EJECTCD"]["long"],
                         {"type": "keys", "keys": ["KEY_LEFTALT", "KEY_F4"]})
        self.assertEqual(len(errors), 5)

    def test_combo_limits(self):
        self.assertIsNone(core.parse_combo("KEY_A+KEY_B+KEY_C+KEY_D+KEY_E"))
        self.assertEqual(core.parse_combo("KEY_LEFTCTRL + KEY_C"), ["KEY_LEFTCTRL", "KEY_C"])


class Routing(unittest.TestCase):
    def test_tap_is_held_between_down_and_up(self):
        router, out, _ = make_router({"KEY_SELECT": {"tap": "KEY_ENTER"}})
        router.on_event("KEY_SELECT", "DOWN", True)
        router.on_event("KEY_SELECT", "UP", True)
        self.assertEqual(out.events, [("KEY_ENTER", 1), ("KEY_ENTER", 0)])

    def test_inactive_source_injects_nothing(self):
        router, out, _ = make_router({"KEY_SELECT": {"tap": "KEY_ENTER"}})
        router.on_event("KEY_SELECT", "DOWN", False)
        router.on_event("KEY_SELECT", "UP", False)
        self.assertEqual(out.events, [])

    def test_up_after_source_lost_still_releases(self):
        router, out, _ = make_router({"KEY_UP": {"tap": "KEY_UP"}})
        router.on_event("KEY_UP", "DOWN", True)
        router.on_event("KEY_UP", "UP", False)
        self.assertEqual(out.events, [("KEY_UP", 1), ("KEY_UP", 0)])

    def test_combo_tap_releases_in_reverse(self):
        router, out, _ = make_router({"KEY_1": {"tap": "KEY_LEFTCTRL+KEY_C"}})
        router.on_event("KEY_1", "DOWN", True)
        router.on_event("KEY_1", "UP", True)
        self.assertEqual(out.events, [("KEY_LEFTCTRL", 1), ("KEY_C", 1),
                                      ("KEY_C", 0), ("KEY_LEFTCTRL", 0)])

    def test_long_only_short_tap_does_nothing(self):
        router, out, _ = make_router({"KEY_EJECTCD": {"long": "KEY_LEFTALT+KEY_F4", "hold": 0.3}})
        router.on_event("KEY_EJECTCD", "DOWN", True)
        router.on_event("KEY_EJECTCD", "UP", True)
        time.sleep(0.5)
        self.assertEqual(out.events, [])

    def test_long_fires_once_after_hold(self):
        router, out, _ = make_router({"KEY_EJECTCD": {"long": "KEY_LEFTALT+KEY_F4", "hold": 0.3}})
        router.on_event("KEY_EJECTCD", "DOWN", True)
        time.sleep(0.6)
        router.on_event("KEY_EJECTCD", "UP", True)
        self.assertEqual(out.events, [("KEY_LEFTALT", 1), ("KEY_F4", 1),
                                      ("KEY_F4", 0), ("KEY_LEFTALT", 0)])

    def test_tap_and_long_on_same_key(self):
        router, out, _ = make_router(
            {"KEY_8": {"tap": "KEY_8", "long": "KEY_LEFTCTRL+KEY_8", "hold": 0.3}})
        router.on_event("KEY_8", "DOWN", True)      # short press -> click of the tap action
        router.on_event("KEY_8", "UP", True)
        self.assertEqual(out.events, [("KEY_8", 1), ("KEY_8", 0)])
        out.events.clear()
        router.on_event("KEY_8", "DOWN", True)      # long press -> only the long action
        time.sleep(0.6)
        router.on_event("KEY_8", "UP", True)
        self.assertEqual(out.events, [("KEY_LEFTCTRL", 1), ("KEY_8", 1),
                                      ("KEY_8", 0), ("KEY_LEFTCTRL", 0)])

    def test_release_all(self):
        router, out, _ = make_router({"KEY_UP": {"tap": "KEY_UP"}})
        router.on_event("KEY_UP", "DOWN", True)
        router.release_all()
        self.assertEqual(out.events, [("KEY_UP", 1), ("KEY_UP", 0)])


class Actions(unittest.TestCase):
    def test_parse_action_kinds(self):
        self.assertEqual(core.parse_action("KEY_A"), {"type": "keys", "keys": ["KEY_A"]})
        self.assertEqual(core.parse_action("app:kodi.desktop"), {"type": "app", "id": "kodi.desktop"})
        self.assertEqual(core.parse_action("url:https://example.org/a?b=1;c"),
                         {"type": "url", "url": "https://example.org/a?b=1;c"})
        self.assertEqual(core.action_to_text(core.parse_action("app:kodi.desktop")), "app:kodi.desktop")

    def test_dangerous_input_is_rejected(self):
        for bad in ("url:javascript:alert(1)", "url:file:///etc/passwd", "url:http://a b/",
                    "url:https://x\nhttps://y", "url:ftp://example.org/", "app:../../bin/sh.desktop",
                    "app:kodi", "app:a b.desktop", "app:$(id).desktop", "app:", "url:", "ls -la",
                    "KEY_A;reboot"):
            self.assertIsNone(core.parse_action(bad), bad)

    def test_desktop_path_rejects_traversal(self):
        self.assertIsNone(core.desktop_path("../../etc/passwd"))
        self.assertIsNone(core.desktop_path("nonexistent-app.desktop"))

    def test_app_tap_launches_once_on_down_and_debounces(self):
        router, out, _ = make_router({"KEY_FIND": {"tap": "app:kodi.desktop"}})
        router.on_event("KEY_FIND", "DOWN", True)
        router.on_event("KEY_FIND", "UP", True)
        router.on_event("KEY_FIND", "DOWN", True)   # within the debounce window: ignored
        router.on_event("KEY_FIND", "UP", True)
        self.assertEqual(router.launches, [{"type": "app", "id": "kodi.desktop"}])
        self.assertEqual(out.events, [])

    def test_launch_not_fired_when_source_inactive(self):
        router, _, _ = make_router({"KEY_FIND": {"tap": "url:https://example.org/"}})
        router.on_event("KEY_FIND", "DOWN", False)
        router.on_event("KEY_FIND", "UP", False)
        self.assertEqual(router.launches, [])

    def test_url_on_long_press_and_key_on_tap(self):
        router, out, _ = make_router({"KEY_8": {"tap": "KEY_8", "long": "url:https://example.org/",
                                                "hold": 0.3}})
        router.on_event("KEY_8", "DOWN", True)
        router.on_event("KEY_8", "UP", True)        # short -> key click only
        self.assertEqual(out.events, [("KEY_8", 1), ("KEY_8", 0)])
        self.assertEqual(router.launches, [])
        router.on_event("KEY_8", "DOWN", True)      # long -> launch only
        time.sleep(0.6)
        router.on_event("KEY_8", "UP", True)
        self.assertEqual(router.launches, [{"type": "url", "url": "https://example.org/"}])
        self.assertEqual(out.events, [("KEY_8", 1), ("KEY_8", 0)])


@unittest.skipIf(core.e is None, "python-evdev not installed")
class Capabilities(unittest.TestCase):
    def test_key_codes_cover_numbers_and_exclude_power(self):
        codes = set(core.key_codes())
        for name in ("KEY_ENTER", "KEY_0", "KEY_9", "KEY_F4", "KEY_LEFTMETA", "KEY_NEXTSONG"):
            self.assertIn(getattr(core.e, name), codes, name)
        self.assertNotIn(core.e.KEY_POWER, codes)
        self.assertNotIn(0, codes)


class Reload(unittest.TestCase):
    def test_defaults_without_file_then_reload(self):
        router, out, path = make_router(None)
        self.assertIn("KEY_SELECT", router.keymap.entries)
        path.write_text(json.dumps({"version": 1, "keys": {"KEY_8": {"tap": "KEY_8"}}}))
        time.sleep(1.1)
        router.on_event("KEY_8", "DOWN", True)
        self.assertEqual(out.events, [("KEY_8", 1)])

    def test_broken_file_keeps_previous_mapping(self):
        router, out, path = make_router({"KEY_8": {"tap": "KEY_8"}})
        path.write_text("{ not json")
        time.sleep(1.1)
        router.keymap.refresh(force=True)
        self.assertIn("KEY_8", router.keymap.entries)


if __name__ == "__main__":
    unittest.main()
