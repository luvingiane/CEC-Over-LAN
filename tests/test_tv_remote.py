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


def bare_out(flag):
    """An Out without a uinput device (so it runs anywhere), driven by a DryFlag."""
    out = object.__new__(core.Out)
    out.cli_dry, out.flag, out.pressed, out.ui = False, flag, set(), None
    return out


class DryRun(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "dry-run"
        self.flag = core.DryFlag(self.path)

    def tick(self):
        time.sleep(0.6)  # the flag file is re-checked every 0.5 s

    def test_flag_follows_the_file(self):
        self.assertFalse(self.flag.on)
        self.path.touch()
        self.tick()
        self.assertTrue(self.flag.on)
        self.path.unlink()
        self.tick()
        self.assertFalse(self.flag.on)

    def test_keys_are_not_pressed_while_dry(self):
        out = bare_out(self.flag)
        self.path.touch()
        self.tick()
        out.key("KEY_ENTER", 1)
        out.key("KEY_ENTER", 0)
        self.assertEqual(out.pressed, set())

    def test_keys_are_pressed_when_not_dry(self):
        out = bare_out(self.flag)
        out.key("KEY_ENTER", 1)
        self.assertEqual(out.pressed, {"KEY_ENTER"})
        out.key("KEY_ENTER", 0)
        self.assertEqual(out.pressed, set())

    def test_key_already_down_is_released_when_dry_turns_on(self):
        out = bare_out(self.flag)
        out.key("KEY_UP", 1)              # really pressed
        self.path.touch()
        self.tick()
        out.key("KEY_UP", 0)              # must still go through, or the key would stay stuck
        self.assertEqual(out.pressed, set())
        out.key("KEY_DOWN", 1)            # a new press is only logged
        self.assertEqual(out.pressed, set())

    def test_cli_dry_run_always_dry(self):
        out = core.Out(True)
        self.assertTrue(out.is_dry())


def seen_lines(router, *events):
    """Feed (key, state, active) events and return the [seen] log lines."""
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        for ev in events:
            router.on_event(*ev)
    return [ln for ln in buf.getvalue().splitlines() if ln.startswith("[seen]")]


class LogFormat(unittest.TestCase):
    def test_off_screen_is_flagged_and_not_applied(self):
        router, out, _ = make_router({"KEY_SELECT": {"tap": "KEY_ENTER"}})
        lines = seen_lines(router, ("KEY_SELECT", "DOWN", False), ("KEY_SELECT", "UP", False))
        self.assertIn("screen=no", lines[0])
        self.assertIn("decision=off-screen", lines[0])
        self.assertIn("screen=no", lines[1])
        self.assertEqual(out.events, [])

    def test_decision_and_release_details(self):
        router, _, _ = make_router({"KEY_SELECT": {"tap": "KEY_ENTER"}})
        lines = seen_lines(router, ("KEY_SELECT", "DOWN", True), ("KEY_SELECT", "UP", True),
                           ("KEY_X", "DOWN", True))
        self.assertIn("screen=yes decision=tap:KEY_ENTER dry=no", lines[0])
        self.assertRegex(lines[1], r"held=\d+\.\d\d result=released")
        self.assertIn("decision=unmapped", lines[2])

    def test_long_press_results(self):
        router, _, _ = make_router({"KEY_EJECTCD": {"long": "KEY_LEFTALT+KEY_F4", "hold": 0.3},
                                    "KEY_FIND": {"tap": "url:https://example.org/path?x=1",
                                                 "long": "app:kodi.desktop", "hold": 0.3}})
        short = seen_lines(router, ("KEY_EJECTCD", "DOWN", True), ("KEY_EJECTCD", "UP", True))
        self.assertIn("decision=wait-long-0.3s:KEY_LEFTALT+KEY_F4", short[0])
        self.assertIn("result=short-press-no-action", short[1])
        longp = []
        longp += seen_lines(router, ("KEY_EJECTCD", "DOWN", True))
        time.sleep(0.6)
        longp += seen_lines(router, ("KEY_EJECTCD", "UP", True))
        self.assertIn("result=long-fired", longp[1])
        both = seen_lines(router, ("KEY_FIND", "DOWN", True))
        self.assertIn("tap-on-release:url:example.org", both[0])  # URL reduced to the host, no spaces
        self.assertNotIn(" ", both[0].split("decision=")[1].split(" dry=")[0])

    def test_web_monitor_parses_the_new_format(self):
        import os
        import tv_remote_web as web
        f = Path(tempfile.mkdtemp()) / "ev.txt"
        f.write_text("100.5 [seen] KEY_SELECT DOWN screen=yes decision=tap:KEY_ENTER dry=yes\n"
                     "100.7 [seen] KEY_SELECT UP screen=yes held=0.20 result=released\n"
                     "101.0 [seen] KEY_EXIT DOWN screen=no decision=off-screen dry=no\n"
                     "102.0 [seen] KEY_OLD DOWN\n")
        os.environ["CEC_EVENTS_FILE"] = str(f)
        try:
            got = web.monitor({"after": ["0"]})["events"]
            self.assertEqual(len(got), 4)
            self.assertEqual((got[0]["key"], got[0]["state"], got[0]["screen"], got[0]["dry"],
                              got[0]["decision"]), ("KEY_SELECT", "DOWN", True, True, "tap:KEY_ENTER"))
            self.assertEqual((got[1]["held"], got[1]["result"]), (0.2, "released"))
            self.assertEqual((got[2]["screen"], got[2]["decision"]), (False, "off-screen"))
            self.assertIsNone(got[3]["screen"])           # old format: no fields, no crash
            self.assertEqual([e["key"] for e in web.monitor({"after": ["100.6"]})["events"]],
                             ["KEY_SELECT", "KEY_EXIT", "KEY_OLD"])
            self.assertEqual([e["key"] for e in web.events({"after": ["0"]})["events"]],
                             ["KEY_SELECT", "KEY_EXIT", "KEY_OLD"])  # wizard feed still works
        finally:
            del os.environ["CEC_EVENTS_FILE"]


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
