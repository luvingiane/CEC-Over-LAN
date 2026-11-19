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


class FakePointerUI:
    def __init__(self):
        self.events = []

    def write(self, etype, code, value):
        self.events.append((etype, code, value))

    def syn(self):
        self.events.append("syn")


@unittest.skipIf(core.e is None, "python-evdev not installed")
class PointerTests(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        self.keep_path, self.dry_path = d / "pointer-visible", d / "dry-run"
        self.keep = core.KeepPointerFlag(self.keep_path)
        self.out = bare_out(core.DryFlag(self.dry_path))
        self.ui = FakePointerUI()
        self.pointer = core.Pointer(self.out, self.keep, self.ui)

    def moves(self, pointer):
        pointer.last = -99.0
        self.ui.events.clear()
        self.assertEqual(pointer.hide(), "moved")
        rel = [ev for ev in self.ui.events if ev != "syn"]
        self.assertTrue(rel and all(t == core.e.EV_REL for t, _, _ in rel))
        x = {v > 0 for _, c, v in rel if c == core.e.REL_X}
        y = {v > 0 for _, c, v in rel if c == core.e.REL_Y}
        self.assertEqual((len(x), len(y)), (1, 1))        # every move goes the same way on each axis
        return x.pop(), y.pop()                           # (right?, down?)

    def test_default_corner_is_top_right_not_the_bottom_edge(self):
        # a bottom dock with auto-hide is revealed by pushing against the bottom edge
        self.assertEqual(self.moves(self.pointer), (True, False))

    def test_every_corner_goes_the_right_way(self):
        for corner, expected in (("top-right", (True, False)), ("bottom-right", (True, True)),
                                 ("top-left", (False, False)), ("bottom-left", (False, True))):
            p = core.Pointer(self.out, self.keep, self.ui, corner)
            self.assertEqual(self.moves(p), expected, corner)

    def test_throttled(self):
        self.assertEqual(self.pointer.hide(), "moved")
        n = len(self.ui.events)
        self.assertEqual(self.pointer.hide(), "skipped")
        self.assertEqual(len(self.ui.events), n)

    def test_left_alone_when_the_owner_wants_it(self):
        self.keep_path.touch()
        time.sleep(0.6)
        self.assertEqual(self.pointer.hide(), "kept")
        self.assertEqual(self.ui.events, [])

    def test_nothing_in_dry_run(self):
        self.dry_path.touch()
        time.sleep(0.6)
        self.assertEqual(self.pointer.hide(), "dry")
        self.assertEqual(self.ui.events, [])

    def test_no_device_means_skipped(self):
        self.assertEqual(core.Pointer(self.out, self.keep, None).hide(), "skipped")


class RouterPointer(unittest.TestCase):
    class Spy:
        def __init__(self):
            self.calls = 0

        def hide(self):
            self.calls += 1
            return "moved"

    def test_pointer_only_on_press_and_only_on_screen(self):
        router, _, _ = make_router({"KEY_SELECT": {"tap": "KEY_ENTER"}})
        router.pointer = self.Spy()
        lines = seen_lines(router, ("KEY_SELECT", "DOWN", True), ("KEY_SELECT", "UP", True),
                           ("KEY_SELECT", "DOWN", False), ("KEY_X", "DOWN", True))
        self.assertIn("pointer=moved", lines[0])
        self.assertNotIn("pointer=", lines[1])      # release
        self.assertNotIn("pointer=", lines[2])      # machine not on screen
        self.assertIn("pointer=moved", lines[3])    # unmapped keys hide it too
        self.assertEqual(router.pointer.calls, 2)


class FakeFocus:
    def __init__(self, **fields):
        self.f = {"pid": 1, "class": "", "app_id": "", "title": "", "comm": ""}
        self.f.update(fields)

    def current(self):
        return self.f

    def restore_soon(self, delay=0.25):
        return False


BACK ={"KEY_BACK": {"tap": "KEY_ESC", "apps": {"es-de": {"tap": "KEY_BACKSPACE"},
                                                "Kodi": {"tap": "KEY_BACKSPACE"}}}}


class AppRules(unittest.TestCase):
    def router(self, focus):
        router, out, _ = make_router(BACK)
        router.focus = focus
        return router, out

    def press(self, router, focus=None, new_focus=None):
        router.on_event("KEY_BACK", "DOWN", True)
        if new_focus is not None:
            router.focus = new_focus
        router.on_event("KEY_BACK", "UP", True)

    def test_default_action_for_other_apps(self):
        router, out = self.router(FakeFocus(**{"class": "gnome-shell"}))
        self.press(router)
        self.assertEqual(out.events, [("KEY_ESC", 1), ("KEY_ESC", 0)])

    def test_exception_matches_process_name_class_and_title_case_insensitively(self):
        for fields in ({"comm": "es-de"}, {"class": "ES-DE"}, {"title": "My Kodi window"},
                       {"app_id": "org.xbmc.KODI"}):
            router, out = self.router(FakeFocus(**fields))
            self.press(router)
            self.assertEqual(out.events, [("KEY_BACKSPACE", 1), ("KEY_BACKSPACE", 0)], fields)

    def test_no_focus_information_uses_the_default(self):
        router, out = self.router(type("NoFocus", (), {"current": lambda self: None,
                                                       "restore_soon": lambda self, delay=0.25: False})())
        self.press(router)
        self.assertEqual(out.events, [("KEY_ESC", 1), ("KEY_ESC", 0)])

    def test_the_same_press_from_two_sources_acts_once(self):
        router, out, _ = make_router({"KEY_SELECT": {"tap": "KEY_ENTER"}})
        router.on_event("KEY_SELECT", "DOWN", True)
        router.on_event("KEY_SELECT", "DOWN", True)      # the second input device, or adb next to the app
        router.on_event("KEY_SELECT", "UP", True)
        router.on_event("KEY_SELECT", "DOWN", True)      # a real second press, after the release
        self.assertEqual(out.events, [("KEY_ENTER", 1), ("KEY_ENTER", 0), ("KEY_ENTER", 1)])

    def test_release_uses_the_entry_chosen_at_press(self):
        router, out = self.router(FakeFocus(comm="es-de"))
        self.press(router, new_focus=FakeFocus(**{"class": "gnome-shell"}))  # focus moves mid-press
        self.assertEqual(out.events, [("KEY_BACKSPACE", 1), ("KEY_BACKSPACE", 0)])

    def test_the_name_shown_in_the_log_matches_the_window(self):
        """The monitor prints YouTube_TV; the window class is "YouTube TV"."""
        router, out, _ = make_router({"KEY_BACK": {"tap": "KEY_BACKSPACE", "apps": {"YouTube_TV": {"tap": "KEY_LEFT"}}}})
        router.focus = FakeFocus(**{"class": "YouTube TV"})
        self.press(router)
        self.assertEqual(out.events[0], ("KEY_LEFT", 1))

    def test_log_shows_the_app_and_the_rule(self):
        router, _ = self.router(FakeFocus(comm="es-de"))
        lines = seen_lines(router, ("KEY_BACK", "DOWN", True))
        self.assertIn("decision=tap:KEY_BACKSPACE@es-de", lines[0])
        self.assertIn("app=es-de", lines[0])

    def test_invalid_rules_are_rejected(self):
        for apps in ({"x": {"tap": "KEY_POWER"}}, {"": {"tap": "KEY_A"}}, {"x": {}},
                     {f"r{i}": {"tap": "KEY_A"} for i in range(11)}, ["not", "a", "dict"],
                     {"a\nb": {"tap": "KEY_A"}}):
            entries, errors = core.normalise({"KEY_BACK": {"tap": "KEY_ESC", "apps": apps}})
            self.assertTrue(errors and "KEY_BACK" not in entries, apps)

    def test_saving_keeps_the_app_rules(self):
        import tv_remote_web as web
        entries, errors = core.normalise(BACK)
        self.assertEqual(errors, [])
        saved = web.to_file_format(entries)
        self.assertEqual(saved["KEY_BACK"]["tap"], "KEY_ESC")
        self.assertEqual(saved["KEY_BACK"]["apps"], {"es-de": {"tap": "KEY_BACKSPACE"},
                                                     "Kodi": {"tap": "KEY_BACKSPACE"}})
        again, errors = core.normalise(saved)          # and it reads back the same
        self.assertEqual(errors, [])
        self.assertEqual([p for p, _ in again["KEY_BACK"]["apps"]], ["es-de", "Kodi"])


import tv_osk as osk  # noqa: E402


class OskModelTests(unittest.TestCase):
    def test_starts_on_q_and_wraps_horizontally(self):
        m = osk.OskModel()
        self.assertEqual(m.key[2], "q")
        m.move("left")
        self.assertEqual(m.key[2], "p")
        m.move("right")
        self.assertEqual(m.key[2], "q")

    def test_vertical_moves_follow_the_horizontal_position_and_wrap(self):
        m = osk.OskModel()
        m.move("down")
        self.assertEqual(m.key[2], "a")
        m.move("up")
        self.assertEqual(m.key[2], "q")
        for _ in range(len(m.rows)):          # a full turn comes back to the same row
            m.move("down")
        self.assertEqual((m.row, m.key[2]), (1, "q"))
        m.col = 9                              # 'p' is at the far right: below it must be a right-hand key
        m.move("down")
        self.assertGreaterEqual(osk.centre(m.rows[m.row], m.col), 0.85)

    def test_char_press_and_one_shot_shift(self):
        m = osk.OskModel()
        self.assertEqual(m.press(), {"type": "char", "char": "q"})
        m.row, m.col = 3, 0                    # shift key
        self.assertIsNone(m.press())
        self.assertEqual(m.layer, "upper")
        m.row, m.col = 1, 0
        self.assertEqual(m.press(), {"type": "char", "char": "Q"})
        self.assertEqual(m.layer, "lower")     # back to lower case after one capital letter

    def test_symbols_layer_and_special_keys(self):
        m = osk.OskModel()
        m.row, m.col = 4, 0                    # ?123
        self.assertIsNone(m.press())
        self.assertEqual(m.layer, "symbols")
        self.assertEqual(m.key[1], "layer")
        m.press()                              # ABC
        self.assertEqual(m.layer, "lower")
        for col, kind in ((2, "space"), (1, "left"), (3, "right"), (4, "close")):
            m.row, m.col = 4, col
            self.assertEqual(m.press(), {"type": kind})
        m.row, m.col = 0, 10
        self.assertEqual(m.press(), {"type": "backspace"})
        m.row, m.col = 2, 9
        self.assertEqual(m.press(), {"type": "enter"})

    def test_every_layer_has_the_same_number_of_rows(self):
        self.assertEqual({len(rows) for rows in osk.LAYERS.values()}, {5})

    def test_every_character_can_be_typed_in_both_layouts(self):
        for layout, table in osk.TABLES.items():
            missing = sorted(c for c in osk.all_layout_chars() if c not in table)
            self.assertEqual(missing, [], layout)

    @unittest.skipIf(core.e is None, "python-evdev not installed")
    def test_typing_tables_only_use_real_key_names(self):
        names = set(osk.SPECIAL.values())
        for table in osk.TABLES.values():
            for key, mods in table.values():
                names.update((key, *mods))
        self.assertEqual([n for n in names if not hasattr(core.e, n)], [])

    @unittest.skipIf(core.e is None, "python-evdev not installed")
    def test_typist_presses_modifiers_around_the_key(self):
        t = osk.Typist("it")
        t.e, t.ui = core.e, FakePointerUI()
        t.do({"type": "char", "char": "A"})
        seq = [(c, v) for _, c, v in (ev for ev in t.ui.events if ev != "syn")]
        e = core.e
        self.assertEqual(seq, [(e.KEY_LEFTSHIFT, 1), (e.KEY_A, 1), (e.KEY_A, 0), (e.KEY_LEFTSHIFT, 0)])
        t.ui.events.clear()
        t.do({"type": "char", "char": "/"})     # Italian layout: Shift+7
        self.assertEqual([c for _, c, v in (ev for ev in t.ui.events if ev != "syn") if v == 1],
                         [e.KEY_LEFTSHIFT, e.KEY_7])

    def test_key_size_is_the_one_the_owner_approved(self):
        # on the owner's 1712 px wide screen a letter key is 2 units = 102 px (the first version)
        self.assertEqual(osk.key_unit(1712), 51)
        self.assertEqual(osk.key_unit(1712) * 2, 102)
        self.assertEqual(osk.key_unit(300), 34)        # never smaller than a readable minimum

    def test_the_widest_row_fits_on_normal_screens(self):
        for width in (1280, 1712, 1920, 3840):
            widest = max(sum(k[3] for k in row) * osk.key_unit(width) + osk.GAP * (len(row) - 1)
                         for rows in osk.LAYERS.values() for row in rows)
            self.assertLess(widest, width, width)

    def test_layout_can_be_forced(self):
        import os
        os.environ["CEC_OSK_LAYOUT"] = "us"
        try:
            self.assertEqual(osk.detect_layout(), "us")
        finally:
            del os.environ["CEC_OSK_LAYOUT"]


class FakeProc:
    def __init__(self):
        self.alive = True

    def poll(self):
        return None if self.alive else 0


class OskControllerTests(unittest.TestCase):
    def make(self):
        sent, procs = [], []

        def starter():
            procs.append(FakeProc())
            return procs[-1]

        return core.OskController(starter=starter, sender=sent.append), sent, procs

    def test_route_only_while_open_and_only_for_the_dpad(self):
        c, sent, procs = self.make()
        self.assertIsNone(c.route("KEY_UP"))                 # closed: nothing is routed
        self.assertEqual(c.toggle(), "opening")
        self.assertEqual(c.route("KEY_UP"), "osk:up")
        self.assertEqual(c.route("KEY_SELECT"), "osk:ok")
        self.assertEqual(c.route("KEY_BACK"), "osk:backspace")   # Back deletes
        self.assertEqual(c.route("KEY_EXIT"), "osk:close")       # Exit leaves the keyboard
        self.assertIsNone(c.route("KEY_PLAYPAUSE"))          # other buttons keep their normal mapping
        self.assertEqual(sent, ["up", "ok", "backspace", "close"])
        self.assertEqual(c.toggle(), "closing")
        self.assertEqual(sent[-1], "close")

    def test_it_stops_routing_when_the_keyboard_process_ends(self):
        c, sent, procs = self.make()
        c.toggle()
        procs[0].alive = False                               # closed itself (Back)
        self.assertFalse(c.active)
        self.assertIsNone(c.route("KEY_UP"))
        self.assertEqual(c.toggle(), "opening")              # and can be opened again
        self.assertEqual(len(procs), 2)

    def test_router_toggles_and_routes_without_injecting_the_dpad(self):
        router, out, _ = make_router({"KEY_FIND": {"tap": "osk:toggle"}, "KEY_UP": {"tap": "KEY_UP"}})
        c, sent, procs = self.make()
        router.osk = c
        lines = seen_lines(router, ("KEY_FIND", "DOWN", True), ("KEY_FIND", "UP", True),
                           ("KEY_UP", "DOWN", True), ("KEY_UP", "UP", True))
        self.assertEqual(len(procs), 1)                      # opened once
        self.assertIn("decision=tap:osk:toggle", lines[0])
        self.assertIn("decision=osk:up", lines[2])
        self.assertEqual(sent, ["up"])
        self.assertEqual(out.events, [])                     # the arrow was NOT injected into the app

    def test_dry_run_never_starts_the_keyboard(self):
        router, out, _ = make_router({"KEY_FIND": {"tap": "osk:toggle"}})
        out.is_dry = lambda: True
        c, sent, procs = self.make()
        router.osk = c
        seen_lines(router, ("KEY_FIND", "DOWN", True))
        self.assertEqual(procs, [])

    def test_osk_action_is_valid_and_round_trips(self):
        self.assertEqual(core.parse_action("osk:toggle"), {"type": "osk"})
        self.assertEqual(core.action_to_text({"type": "osk"}), "osk:toggle")
        self.assertIsNone(core.parse_action("osk:evil"))


class RemoteModel(unittest.TestCase):
    def setUp(self):
        import tv_remote_web as web
        self.web = web
        self.old = web.LEARNED_FILE
        web.LEARNED_FILE = Path(tempfile.mkdtemp()) / "remote-learned.json"

    def tearDown(self):
        self.web.LEARNED_FILE = self.old

    def test_the_model_is_consistent(self):
        m = self.web.remote_model()
        self.assertTrue(m["ok"])
        buttons = m["buttons"]
        self.assertEqual(buttons["ok"]["code"], "KEY_SELECT")
        self.assertEqual(buttons["back"]["code"], "KEY_BACK")
        codes = [b["code"] for b in buttons.values() if b["code"]]
        self.assertEqual(len(codes), len(set(codes)), "two buttons share a code")
        for code in codes:
            self.assertRegex(code, r"^KEY_[A-Z0-9_]+$")
        for bid in ("up", "down", "left", "right", "ok", "back", "home", "exit", "red", "green",
                    "yellow", "blue", "n0", "n9", "volup", "chup"):
            self.assertIn(bid, buttons)

    def test_the_known_codes_agree_with_what_the_logs_showed(self):
        b = self.web.remote_model()["buttons"]
        self.assertEqual([b[k]["code"] for k in ("red", "green", "yellow", "blue")],
                         ["KEY_STOPCD", "KEY_EJECTCD", "KEY_SHUFFLE", "KEY_SEARCH"])
        self.assertEqual(b["exit"]["code"], "KEY_EXIT")
        self.assertIsNone(b["mic"]["code"])          # unknown until learned, never invented
        self.assertEqual(b["mute"]["code"], "KEY_MUTE")
        self.assertTrue(b["netflix"]["guess"])       # read off the order of a test, not confirmed

    def test_learning_a_button_and_going_back(self):
        self.assertTrue(self.web.do_remote({"id": "info", "code": "KEY_INFO"})["ok"])
        info = self.web.remote_model()["buttons"]["info"]
        self.assertEqual((info["code"], info["learned"]), ("KEY_INFO", True))
        self.web.do_remote({"id": "ok", "code": "KEY_ENTER"})       # a learned code replaces the default
        self.assertEqual(self.web.remote_model()["buttons"]["ok"]["code"], "KEY_ENTER")
        self.web.do_remote({"id": "ok", "reset": True})             # and can be reset
        self.assertEqual(self.web.remote_model()["buttons"]["ok"]["code"], "KEY_SELECT")

    def test_a_code_belongs_to_one_button(self):
        self.web.do_remote({"id": "mic", "code": "KEY_PROG1"})
        self.web.do_remote({"id": "settings", "code": "KEY_PROG1"})
        b = self.web.remote_model()["buttons"]
        self.assertEqual(b["settings"]["code"], "KEY_PROG1")
        self.assertIsNone(b["mic"]["code"])                         # the first one is unlearned again

    def test_bad_input_is_rejected(self):
        for bad in ({"id": "nope", "code": "KEY_A"}, {"id": "info", "code": "rm -rf"},
                    {"id": "info", "code": None}, {"id": "info"}, {"code": "KEY_A"}):
            self.assertFalse(self.web.do_remote(bad)["ok"], bad)


class CustomRemote(unittest.TestCase):
    """A remote described by the owner's AI (REMOTE-PROMPT) replaces the built-in drawing."""

    def setUp(self):
        import tv_remote_web as web
        self.web = web
        self.old = (web.LEARNED_FILE, web.CUSTOM_REMOTE_FILE)
        tmp = Path(tempfile.mkdtemp())
        web.LEARNED_FILE, web.CUSTOM_REMOTE_FILE = tmp / "remote-learned.json", tmp / "remote.json"
        self.model = json.loads((Path(web.__file__).parent / "web" / "remote-prompt.md").read_text()
                                .split("## Example")[1].split("```")[1])

    def tearDown(self):
        self.web.LEARNED_FILE, self.web.CUSTOM_REMOTE_FILE = self.old

    def test_the_example_in_the_prompt_is_valid(self):
        self.assertIsNone(self.web.validate_remote(self.model))

    def test_builtin_model_is_valid_and_every_button_is_in_the_layout(self):
        m = json.loads(self.web.BUILTIN_REMOTE_FILE.read_text())
        self.assertIsNone(self.web.validate_remote(m))
        used = set()
        for row in m["layout"]:
            if "dpad" in row:
                used |= set(row["dpad"].values())
            for it in row.get("items", []):
                used |= set([it] if isinstance(it, str) else next(iter(it.values())))
        self.assertEqual(used, set(m["buttons"]))

    def test_importing_and_resetting(self):
        self.assertTrue(self.web.do_remote_model({"model": json.dumps(self.model)})["ok"])
        m = self.web.remote_model()
        self.assertTrue(m["custom"])
        self.assertEqual(m["name"], "Acme Streamer remote")
        self.assertEqual((m["buttons"]["up"]["kind"], m["buttons"]["info"]["kind"], m["buttons"]["red"]["kind"],
                          m["buttons"]["red"]["color"]), ("round", "pill", "col", "red"))
        self.assertTrue(self.web.do_remote_model({"reset": True})["ok"])
        self.assertFalse(self.web.remote_model()["custom"])

    def test_importing_forgets_the_codes_learned_on_the_old_remote(self):
        self.web.do_remote({"id": "info", "code": "KEY_INFO"})
        self.web.do_remote_model({"model": self.model})
        self.assertIsNone(self.web.remote_model()["buttons"]["info"]["code"])

    def test_broken_models_are_refused_with_a_reason(self):
        import copy
        def bad(mutate):
            m = copy.deepcopy(self.model)
            mutate(m)
            return self.web.validate_remote(m)
        self.assertIn("not in", bad(lambda m: m["layout"].append({"items": ["ghost"]})))
        self.assertIn("twice", bad(lambda m: m["layout"].append({"items": ["up"]})))
        self.assertIn("same code", bad(lambda m: m["buttons"]["mic"].update(code="KEY_UP")))
        self.assertIn("code", bad(lambda m: m["buttons"]["mic"].update(code="rm -rf")))
        self.assertIn("color", bad(lambda m: m["buttons"]["red"].pop("color")))
        self.assertIn("shape", bad(lambda m: m["buttons"]["mic"].update(shape="triangle")))
        self.assertIn("dpad", bad(lambda m: m["layout"][1]["dpad"].pop("ok")))
        self.assertIn("label", bad(lambda m: m["buttons"]["mic"].update(label="")))
        self.assertFalse(self.web.do_remote_model({"model": "{not json"})["ok"])
        self.assertFalse(self.web.CUSTOM_REMOTE_FILE.exists())


def win(pid, cls="app"):
    return {"pid": pid, "class": cls, "app_id": "", "title": "", "comm": cls}


class FocusRestore(unittest.TestCase):
    """Esc with no window focused (the dock after Ctrl+Alt+Tab) must hand the focus back."""

    def make(self, *answers):
        seq, activated = list(answers), []
        f = core.Focus(query=lambda: seq.pop(0) if len(seq) > 1 else seq[0],
                       activate=lambda pid: activated.append(pid) or True)
        return f, activated

    def tick(self, f):
        f._at = -99.0          # skip the 0.25 s cache

    def test_returns_the_focus_to_the_window_that_had_it(self):
        f, activated = self.make(win(111, "es-de"), win(0, ""))
        f.current(); self.tick(f)              # the press that opens the dock: es-de is focused
        f.current()                            # the next press: no window has the focus any more
        self.assertTrue(f.restore_soon(delay=0.02))
        time.sleep(0.2)
        self.assertEqual(activated, [111])

    def test_nothing_to_do_while_a_window_has_the_focus(self):
        f, activated = self.make(win(111, "es-de"))
        f.current()
        self.assertFalse(f.restore_soon(delay=0.02))
        time.sleep(0.1)
        self.assertEqual(activated, [])

    def test_nothing_to_restore_if_no_window_was_ever_seen(self):
        f, activated = self.make(win(0, ""))
        f.current()
        self.assertFalse(f.restore_soon(delay=0.02))

    def test_no_focus_information_does_nothing(self):
        f, activated = self.make(None)
        f.current()
        self.assertFalse(f.restore_soon(delay=0.02))

    def router(self, raw, *answers, dry=False):
        router, out, _ = make_router(raw)
        f, activated = self.make(*answers)
        f.restore_soon = lambda delay=0.25, _o=f.restore_soon: _o(delay=0.02)   # keep the test fast
        router.focus = f
        if dry:
            out.is_dry = lambda: True
        return router, out, activated

    def test_esc_from_the_remote_restores_the_focus_after_the_dock(self):
        router, out, activated = self.router({"KEY_BACK": {"tap": "KEY_ESC"}}, win(111, "es-de"), win(0, ""))
        router.focus.current()                        # a previous press saw es-de
        router.focus._at = -99.0
        router.on_event("KEY_BACK", "DOWN", True)     # the dock has the focus: pid 0
        router.on_event("KEY_BACK", "UP", True)
        time.sleep(0.2)
        self.assertEqual(out.events, [("KEY_ESC", 1), ("KEY_ESC", 0)])
        self.assertEqual(activated, [111])

    def test_backspace_and_alt_f4_close_the_dock_with_esc(self):
        for name, tap in (("KEY_BACK", "KEY_BACKSPACE"), ("KEY_EXIT", "KEY_LEFTALT+KEY_F4")):
            router, out, activated = self.router({name: {"tap": tap}}, win(111, "es-de"), win(0, ""))
            router.focus.current(); router.focus._at = -99.0
            router.on_event(name, "DOWN", True)       # the dock has the focus
            router.on_event(name, "UP", True)
            time.sleep(0.2)
            self.assertEqual(out.events, [("KEY_ESC", 1), ("KEY_ESC", 0)], tap)
            self.assertEqual(activated, [111], tap)

    def test_backspace_is_left_alone_while_a_window_has_the_focus(self):
        router, out, activated = self.router({"KEY_BACK": {"tap": "KEY_BACKSPACE"}}, win(111, "es-de"))
        router.focus.current(); router.focus._at = -99.0
        router.on_event("KEY_BACK", "DOWN", True)
        router.on_event("KEY_BACK", "UP", True)
        self.assertEqual(out.events, [("KEY_BACKSPACE", 1), ("KEY_BACKSPACE", 0)])
        self.assertEqual(activated, [])

    def test_other_keys_never_restore_the_focus(self):
        router, out, activated = self.router({"KEY_UP": {"tap": "KEY_UP"}}, win(111, "es-de"), win(0, ""))
        router.focus.current(); router.focus._at = -99.0
        router.on_event("KEY_UP", "DOWN", True)
        router.on_event("KEY_UP", "UP", True)
        time.sleep(0.2)
        self.assertEqual(activated, [])

    def test_dry_run_does_not_touch_the_focus(self):
        router, out, activated = self.router({"KEY_BACK": {"tap": "KEY_ESC"}}, win(111, "es-de"), win(0, ""), dry=True)
        router.focus.current(); router.focus._at = -99.0
        router.on_event("KEY_BACK", "DOWN", True)
        router.on_event("KEY_BACK", "UP", True)
        time.sleep(0.2)
        self.assertEqual(activated, [])


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "receiver" / "steam"))
import steam_glyphs as sg  # noqa: E402


class SteamGlyphs(unittest.TestCase):
    def buttons(self):
        import tv_remote_web as web
        old = web.LEARNED_FILE
        web.LEARNED_FILE = Path(tempfile.mkdtemp()) / "none.json"
        try:
            return web.remote_model()["buttons"]
        finally:
            web.LEARNED_FILE = old

    def build(self, raw):
        entries, errors = core.normalise(raw)
        self.assertEqual(errors, [])
        return sg.build_map(entries, self.buttons())

    def test_back_sent_as_esc_shows_the_back_arrow(self):
        m = self.build({"KEY_BACK": {"tap": "KEY_ESC"}, "KEY_SELECT": {"tap": "KEY_ENTER"}})
        self.assertEqual(m["Esc"]["label"], "←")
        self.assertEqual(m["Esc"]["title"], "Back")
        self.assertEqual(m["Enter"]["label"], "OK")

    def test_translated_key_names_are_covered(self):
        m = self.build({"KEY_SELECT": {"tap": "KEY_ENTER"}})
        self.assertEqual(m["Enter"]["label"], m["Invio"]["label"])   # Steam in Italian says "Invio"

    def test_colour_buttons_and_function_keys(self):
        m = self.build({"KEY_STOPCD": {"tap": "KEY_F1"}, "KEY_EJECTCD": {"tap": "KEY_F9"}})
        self.assertEqual((m["F1"]["kind"], m["F1"]["color"]), ("col", "red"))
        self.assertEqual((m["F9"]["kind"], m["F9"]["color"]), ("col", "green"))

    def test_the_exception_for_steam_wins_and_others_are_ignored(self):
        m = self.build({"KEY_BACK": {"tap": "KEY_ESC", "apps": {"es-de": {"tap": "KEY_BACKSPACE"},
                                                                   "steam": {"tap": "KEY_F3"}}}})
        self.assertIn("F3", m)            # while Steam is focused Back sends F3
        self.assertNotIn("Esc", m)
        self.assertNotIn("Backspace", m)  # the es-de exception does not apply to Steam

    def test_long_presses_and_combinations_have_no_key_cap(self):
        m = self.build({"KEY_BACK": {"tap": "KEY_ESC", "long": "KEY_LEFTALT+KEY_F4", "hold": 2},
                        "KEY_SELECT": {"tap": "KEY_LEFTCTRL+KEY_ENTER"},
                        "KEY_UP": {"tap": "app:kodi.desktop"}})
        self.assertEqual(m, {})

    def test_two_buttons_on_the_same_key_the_more_important_wins(self):
        m = self.build({"KEY_STOPCD": {"tap": "KEY_ESC"}, "KEY_BACK": {"tap": "KEY_ESC"}})
        self.assertEqual(m["Esc"]["title"], "Back")

    def test_unknown_codes_and_unmapped_keys_are_skipped(self):
        m = self.build({"KEY_FOO": {"tap": "KEY_ESC"}, "KEY_BACK": {"tap": "KEY_POWER".replace("POWER", "MUTE")}})
        self.assertEqual(m, {})           # KEY_FOO is not a remote button; KEY_MUTE has no Steam label

    def test_the_injected_script_is_valid_json_around_the_map(self):
        js = sg.JS.replace("__MAP__", json.dumps({"Esc": {"label": "←", "kind": "round", "color": "", "title": "Back"}}))
        self.assertIn('"Esc"', js)
        self.assertNotIn("__MAP__", js)


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

    def test_launched_programs_get_their_own_scope(self):
        cmd = core.scope_cmd(["gio", "launch", "/usr/share/applications/x.desktop"])
        self.assertEqual(cmd[-3:], ["gio", "launch", "/usr/share/applications/x.desktop"])
        if len(cmd) > 3:   # systemd-run is installed
            self.assertTrue(cmd[0].endswith("systemd-run"))
            self.assertIn("--scope", cmd)
            self.assertIn("--user", cmd)

    def test_names_people_actually_type_are_accepted(self):
        keys = lambda t: (core.parse_action(t) or {}).get("keys")  # noqa: E731
        self.assertEqual(keys("KEY_META+KEY_S"), ["KEY_LEFTMETA", "KEY_S"])   # the one that failed
        self.assertEqual(keys("Super+S"), ["KEY_LEFTMETA", "KEY_S"])
        self.assertEqual(keys("super + s"), ["KEY_LEFTMETA", "KEY_S"])
        self.assertEqual(keys("Win+Q"), ["KEY_LEFTMETA", "KEY_Q"])
        self.assertEqual(keys("Ctrl+Alt+Tab"), ["KEY_LEFTCTRL", "KEY_LEFTALT", "KEY_TAB"])
        self.assertEqual(keys("alt+f4"), ["KEY_LEFTALT", "KEY_F4"])
        self.assertEqual(keys("KEY_f4"), ["KEY_F4"])
        self.assertEqual(keys("escape"), ["KEY_ESC"])
        self.assertEqual(keys("KEY_LEFTMETA+KEY_S"), ["KEY_LEFTMETA", "KEY_S"])  # canonical form unchanged

    def test_still_refuses_what_is_not_a_key_or_is_dangerous(self):
        for bad in ("Alt+Fx9", "KEY_POWER", "Super+KEY_SLEEP", "a b c d e", "rm -rf ~", "KEY_A;reboot"):
            self.assertIsNone(core.parse_action(bad), bad)

    def test_the_error_says_what_is_wrong(self):
        self.assertIn("unknown key 'KEY_LEFTMETAA'", core.combo_hint("KEY_LEFTMETAA+KEY_S"))
        self.assertIn("KEY_LEFTMETA", core.combo_hint("KEY_LEFTMETAA+KEY_S"))     # and suggests the right one
        self.assertIn("not allowed", core.combo_hint("KEY_POWER"))
        self.assertIn("at most 4", core.combo_hint("A+B+C+D+E"))
        entries, errors = core.normalise({"KEY_X": {"tap": "KEY_LEFTMETAA+KEY_S"}})
        self.assertEqual(entries, {})
        self.assertIn("did you mean KEY_LEFTMETA", errors[0])

    def test_saved_form_is_the_canonical_one(self):
        import tv_remote_web as web
        entries, _ = core.normalise({"KEY_Y": {"tap": "Super+S", "long": "ctrl+alt+tab"}})
        saved = web.to_file_format(entries)["KEY_Y"]
        self.assertEqual(saved["tap"], "KEY_LEFTMETA+KEY_S")
        self.assertEqual(saved["long"], "KEY_LEFTCTRL+KEY_LEFTALT+KEY_TAB")

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


class FakeMouseOut(FakeOut):
    def is_dry(self):
        return False


@unittest.skipIf(core.e is None, "python-evdev not installed")
class MouseActions(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(core.parse_action("mouse:left"), {"type": "mouse", "name": "left"})
        self.assertEqual(core.action_to_text(core.parse_action("mouse:scroll-down")), "mouse:scroll-down")
        self.assertIsNone(core.parse_action("mouse:teleport"))
        self.assertIn("mouse:", core.action_hint("mouse:teleport"))

    def test_click_scroll_and_step(self):
        ui = FakePointerUI()
        m = core.Mouse(FakeMouseOut(), ui)
        m.click("right")
        self.assertEqual(ui.events, [(core.e.EV_KEY, core.e.BTN_RIGHT, 1), "syn",
                                     (core.e.EV_KEY, core.e.BTN_RIGHT, 0), "syn"])
        ui.events.clear()
        m.click("scroll-up")
        self.assertEqual(ui.events[0], (core.e.EV_REL, core.e.REL_WHEEL, 1))
        ui.events.clear()
        m.click("move-left")
        self.assertEqual(ui.events[0], (core.e.EV_REL, core.e.REL_X, -18))

    def test_button_is_held_until_release(self):
        ui = FakePointerUI()
        m = core.Mouse(FakeMouseOut(), ui)
        m.start("KEY_OK", "left")
        self.assertEqual(ui.events[0], (core.e.EV_KEY, core.e.BTN_LEFT, 1))
        m.stop("KEY_OK")
        self.assertIn((core.e.EV_KEY, core.e.BTN_LEFT, 0), ui.events)

    def test_movement_repeats_while_held_and_stops(self):
        ui = FakePointerUI()
        m = core.Mouse(FakeMouseOut(), ui)
        m.start("KEY_UP", "move-up")
        time.sleep(0.2)
        m.stop("KEY_UP")
        moves = [x for x in ui.events if x != "syn" and x[1] == core.e.REL_Y]
        self.assertGreater(len(moves), 3)
        self.assertTrue(all(x[2] < 0 for x in moves))
        self.assertLess(moves[0][2] * -1, moves[-1][2] * -1 + 1)   # it speeds up
        time.sleep(0.1)
        count = len(ui.events)
        time.sleep(0.15)
        self.assertEqual(len(ui.events), count)                    # nothing after the release

    def test_dry_run_moves_nothing(self):
        class Dry(FakeOut):
            def is_dry(self):
                return True
        ui = FakePointerUI()
        m = core.Mouse(Dry(), ui)
        m.click("left")
        m.start("KEY_OK", "left")
        self.assertEqual(ui.events, [])

    def test_router_drives_the_mouse_and_keeps_the_pointer(self):
        router, _, _ = make_router({"KEY_SELECT": {"tap": "mouse:left"}})
        ui = FakePointerUI()
        router.out = FakeMouseOut()
        router.mouse = core.Mouse(router.out, ui)
        class Spy:
            calls = 0
            def hide(self):
                Spy.calls += 1
                return "moved"
        router.pointer = Spy()
        lines = seen_lines(router, ("KEY_SELECT", "DOWN", True), ("KEY_SELECT", "UP", True))
        self.assertIn("decision=tap:mouse:left", lines[0])
        self.assertIn("pointer=mouse", lines[0])
        self.assertEqual(Spy.calls, 0)
        self.assertEqual([x for x in ui.events if x != "syn"],
                         [(core.e.EV_KEY, core.e.BTN_LEFT, 1), (core.e.EV_KEY, core.e.BTN_LEFT, 0)])


AVAHI = ('+;ens18;IPv4;adb-X-aAUnrY;_adb-tls-connect._tcp;local\n'
         '=;ens18;IPv6;adb-X-aAUnrY;_adb-tls-connect._tcp;local;Android.local;fe80::1;45241;"v=1"\n'
         '=;ens18;IPv4;adb-X-aAUnrY;_adb-tls-connect._tcp;local;Android.local;192.168.96.200;45241;"v=1"\n')


class Reconnect(unittest.TestCase):
    class Args:
        target = "192.168.96.200:40000"

    def runner(self, replies):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            out = replies.pop(0) if cmd[1] == "connect" else ""
            return type("R", (), {"stdout": out})()
        run.calls = calls
        return run

    def test_parsers(self):
        self.assertEqual(core.parse_avahi(AVAHI, "_adb-tls-connect._tcp"), [("192.168.96.200", 45241)])
        mdns = "List of discovered mdns services\nadb-X\t_adb-tls-connect._tcp\t192.168.96.200:45241\n"
        self.assertEqual(core.parse_adb_mdns(mdns, "_adb-tls-connect._tcp"), [("192.168.96.200", 45241)])
        self.assertEqual(core.parse_avahi("", "_adb-tls-connect._tcp"), [])

    def test_follows_the_port_when_the_tv_changed_it(self):
        saved, args = [], self.Args()
        run = self.runner(["failed to connect to 192.168.96.200:40000", "connected to 192.168.96.200:45241"])
        ok = core.adb_connect(args, runner=run, discover=lambda h: 45241, save=saved.append)
        self.assertTrue(ok)
        self.assertEqual(args.target, "192.168.96.200:45241")
        self.assertEqual(saved, ["192.168.96.200:45241"])

    def test_nothing_announced_means_not_connected(self):
        args = self.Args()
        run = self.runner(["failed to connect"])
        self.assertFalse(core.adb_connect(args, runner=run, discover=lambda h: None, save=lambda t: None))
        self.assertEqual(args.target, "192.168.96.200:40000")

    def test_a_silent_port_still_triggers_the_lookup(self):
        args, replies = self.Args(), ["silence", "connected to 192.168.96.200:45241"]

        def run(cmd, **kw):
            if cmd[1] == "connect":
                reply = replies.pop(0)
                if reply == "silence":
                    raise core.subprocess.TimeoutExpired(cmd, 15)
                return type("R", (), {"stdout": reply})()
            return type("R", (), {"stdout": ""})()
        self.assertTrue(core.adb_connect(args, runner=run, discover=lambda h: 45241, save=lambda t: None))
        self.assertEqual(args.target, "192.168.96.200:45241")

    def test_same_port_is_not_retried_forever(self):
        args = self.Args()
        run = self.runner(["failed to connect"])
        self.assertFalse(core.adb_connect(args, runner=run, discover=lambda h: 40000, save=lambda t: None))
        self.assertEqual(len([c for c in run.calls if c[1] == "connect"]), 1)

    def test_connected_at_once_looks_nothing_up(self):
        args = self.Args()
        run = self.runner(["already connected to 192.168.96.200:40000"])
        def boom(h):
            raise AssertionError("discovery must not run")
        self.assertTrue(core.adb_connect(args, runner=run, discover=boom, save=boom))

    def test_discover_port_only_for_the_tvs_own_address(self):
        look = lambda s: [("192.168.96.7", 1111), ("192.168.96.200", 45241)]
        self.assertEqual(core.discover_port("192.168.96.200", lookup=look), 45241)
        self.assertIsNone(core.discover_port("192.168.96.99", lookup=look))


import socket as _socket  # noqa: E402
import types  # noqa: E402
import app_source  # noqa: E402


class AppSourceTests(unittest.TestCase):
    """The TV app sends the remote's keys over TCP (receiver/app_source.py)."""

    def make(self, **kw):
        self.keys, self.events = [], []
        src = app_source.AppSource(
            "s3cret", on_key=lambda k, s: self.keys.append((k, s)), port=0, bind="127.0.0.1",
            log=self.events.append, on_connect=lambda: self.events.append("connect"),
            on_disconnect=lambda: self.events.append("disconnect"), **kw)
        port = src.start()
        self.addCleanup(src.stop)
        return src, port

    def client(self, port, hello="HELLO s3cret"):
        s = _socket.create_connection(("127.0.0.1", port), timeout=2)
        self.addCleanup(s.close)
        s.sendall((hello + "\n").encode())
        return s

    def wait(self, cond, secs=2.0):
        end = time.time() + secs
        while time.time() < end and not cond():
            time.sleep(0.01)
        return cond()

    def test_a_wrong_token_is_refused_and_presses_nothing(self):
        src, port = self.make()
        s = self.client(port, "HELLO nope")
        s.sendall(b"KEY_ENTER DOWN\n")
        self.assertEqual(s.recv(10), b"")                 # closed without OK
        time.sleep(0.1)
        self.assertEqual(self.keys, [])
        self.assertFalse(src.connected)

    def test_keys_gate_and_disconnect(self):
        src, port = self.make()
        s = self.client(port)
        self.assertEqual(s.recv(3), b"OK\n")
        s.sendall(b"KEY_STOPCD DOWN\nKEY_STOPCD UP\nPING\nGATE yes\n")
        self.assertTrue(self.wait(lambda: src.gate_state() is True))
        self.assertEqual(self.keys, [("KEY_STOPCD", "DOWN"), ("KEY_STOPCD", "UP")])
        s.sendall(b"GATE no\n")
        self.assertTrue(self.wait(lambda: src.gate_state() is False))
        s.close()
        self.assertTrue(self.wait(lambda: not src.connected))
        self.assertIsNone(src.gate_state())               # nobody to ask any more
        self.assertIn("disconnect", self.events)

    def test_malformed_lines_never_reach_the_router(self):
        src, port = self.make()
        s = self.client(port)
        s.recv(3)
        s.sendall(b"key_enter DOWN\nKEY_ENTER SIDEWAYS\nKEY_ENTER\nrm -rf /\nKEY_ENTER DOWN extra\n")
        s.sendall(b"KEY_ENTER UP\n")
        self.assertTrue(self.wait(lambda: self.keys))
        self.assertEqual(self.keys, [("KEY_ENTER", "UP")])

    def test_window_lines_are_only_logged(self):
        src, port = self.make()
        s = self.client(port); s.recv(3)
        s.sendall(b"WIN com.tv.input HdmiActivity [HDMI 1]\nKEY_UP DOWN\n")
        self.assertTrue(self.wait(lambda: self.keys))
        self.assertIn("[window] com.tv.input HdmiActivity [HDMI 1]", self.events)
        self.assertEqual(self.keys, [("KEY_UP", "DOWN")])

    def test_the_newest_app_replaces_the_old_one(self):
        src, port = self.make()
        a = self.client(port); a.recv(3)
        b = self.client(port); b.recv(3)
        self.assertTrue(self.wait(lambda: a.recv(1) == b""))   # the first one was closed
        b.sendall(b"KEY_UP DOWN\n")
        self.assertTrue(self.wait(lambda: self.keys))
        self.assertTrue(src.connected)

    def test_silence_closes_the_connection(self):
        src, port = self.make(silence_s=0.3)
        s = self.client(port); s.recv(3)
        self.assertTrue(self.wait(lambda: not src.connected, 3.0))

    def test_only_private_addresses(self):
        self.assertTrue(app_source.is_private("192.168.96.200"))
        self.assertTrue(app_source.is_private("127.0.0.1"))
        self.assertFalse(app_source.is_private("8.8.8.8"))
        self.assertFalse(app_source.is_private("not an address"))


class GateFromTheApp(unittest.TestCase):
    class FakeApp:
        def __init__(self, state):
            self.state = state

        def gate_state(self):
            return self.state

    def run_gate(self, state, source):
        args = types.SimpleNamespace(no_gate=False, active_path="0x1000", poll=0.01, dead_after=4, source=source)
        released = []
        gate = core.Gate(args, types.SimpleNamespace(release_all=lambda: released.append(1)))
        gate.read_path = lambda: (_ for _ in ()).throw(AssertionError("adb must not be asked"))
        gate.app = self.FakeApp(state)
        th = core.threading.Thread(target=gate.loop, daemon=True)
        th.start()
        time.sleep(0.15)
        gate.stop.set()
        return gate, released

    def test_the_apps_answer_is_the_gate(self):
        gate, _ = self.run_gate(True, "app")
        self.assertTrue(gate.active)

    def test_app_mode_without_an_answer_does_not_inject_and_does_not_use_adb(self):
        gate, _ = self.run_gate(None, "app")
        self.assertFalse(gate.active)

    def test_the_app_saying_no_releases_the_keys(self):
        gate, released = self.run_gate(False, "app")
        self.assertFalse(gate.active)
        self.assertEqual(released, [])     # it was never active: nothing to release


import tv_info  # noqa: E402


class InfoPanel(unittest.TestCase):
    def test_the_action_is_known(self):
        self.assertEqual(core.parse_action("info:show"), {"type": "info"})
        self.assertEqual(core.action_to_text({"type": "info"}), "info:show")
        self.assertIsNone(core.parse_action("info:everything"))

    def test_toggle_opens_then_closes(self):
        procs = []

        class P:
            alive = True
            def poll(self): return None if self.alive else 0
            def terminate(self): self.alive = False
        c = core.InfoController(starter=lambda: procs.append(P()) or procs[-1])
        self.assertEqual(c.toggle(), "opening")
        self.assertEqual(c.toggle(), "closing")
        self.assertFalse(procs[0].alive)
        self.assertEqual(c.toggle(), "opening")          # a finished panel opens again

    def test_router_opens_it_once_and_not_in_dry_run(self):
        router, out, _ = make_router({"KEY_INFO": {"tap": "info:show"}})
        opened = []
        router.info = types.SimpleNamespace(toggle=lambda: opened.append(1) or "opening")
        router.out = FakeMouseOut()
        router.on_event("KEY_INFO", "DOWN", True); router.on_event("KEY_INFO", "UP", True)
        self.assertEqual(len(opened), 1)
        router2, out2, _ = make_router({"KEY_INFO": {"tap": "info:show"}})
        router2.info = types.SimpleNamespace(toggle=lambda: opened.append(1) or "opening")
        out2.is_dry = lambda: True
        router2.on_event("KEY_INFO", "DOWN", True); router2.on_event("KEY_INFO", "UP", True)
        self.assertEqual(len(opened), 1)

    def test_leaving_the_screen_closes_the_panels(self):
        router, _, _ = make_router({})
        closed = []

        class Panel:
            active = True
            def __init__(self, name): self.name = name
            def toggle(self): closed.append(self.name)
        router.info, router.osk = Panel("info"), Panel("osk")
        router.close_panels()
        self.assertEqual(closed, ["info", "osk"])
        router.info.active = router.osk.active = False
        router.close_panels()
        self.assertEqual(closed, ["info", "osk"])      # nothing open: nothing to close

    def test_readers_with_fake_files(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "up").write_text("93784.12 1000.0\n")
        (tmp / "mem").write_text("MemTotal: 16777216 kB\nMemFree: 1 kB\nMemAvailable: 8388608 kB\n")
        self.assertEqual(tv_info.uptime(tmp / "up"), "1 d 2 h 3 min")
        self.assertEqual(tv_info.memory(tmp / "mem"), "8.0 / 16.0 GB")
        self.assertIsNone(tv_info.uptime(tmp / "missing"))
        self.assertIsNone(tv_info.memory(tmp / "missing"))
        (tmp / "stat").write_text("cpu 100 0 100 800 0 0 0 0\n")
        self.assertIsNone(tv_info.cpu_percent(tmp / "nothere", wait=0))

    @unittest.skipUnless(__import__("importlib").util.find_spec("numpy"), "numpy not installed")
    def test_the_spectrum_puts_a_tone_in_its_band(self):
        import numpy as np
        edges = tv_info.band_edges(n=1024)
        t = np.arange(1024) / 44100
        loud = (np.sin(2 * np.pi * 1000 * t) * 20000).astype("<i2")
        lv = tv_info.levels_from_samples(loud, edges)
        peak = max(range(len(lv)), key=lv.__getitem__)
        centre = (edges[peak] + edges[peak + 1]) / 2 * 44100 / 1024
        self.assertGreater(lv[peak], 0.5)
        self.assertLess(abs(centre - 1000), 400)            # the loudest band holds 1 kHz
        silent = tv_info.levels_from_samples(np.zeros(1024, dtype="<i2"), edges)
        self.assertEqual(max(silent), 0.0)
        fall = tv_info.levels_from_samples(np.zeros(1024, dtype="<i2"), edges, previous=lv)
        self.assertTrue(0 < fall[peak] < lv[peak])          # bars fall smoothly, not at once

    def test_artwork_box_follows_the_shape_and_bars_are_found(self):
        self.assertEqual(tv_info.art_size(600, 600), (240, 240))            # music: square
        self.assertEqual(tv_info.art_size(1280, 720), (426, 240))           # video: 16:9
        self.assertEqual(tv_info.art_size(2000, 100)[0], 426)               # never wider than that
        self.assertEqual(tv_info.art_size(50, 400)[0], 200)                 # nor narrower
        rows = [0.0] * 45 + [90.0] * 270 + [1.0] * 45                       # a 4:3 thumbnail with bars
        self.assertEqual(tv_info.letterbox(rows), (45, 45))
        self.assertEqual(tv_info.letterbox([80.0] * 100), (0, 0))
        self.assertEqual(tv_info.letterbox([0.0] * 90), (30, 30))           # all black: at most a third each way

    @unittest.skipUnless(__import__("importlib").util.find_spec("numpy") and __import__("importlib").util.find_spec("gi"),
                         "numpy or gi not installed")
    def test_black_bars_are_cut_from_a_real_pixbuf(self):
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 40, 40)
        pix.fill(0xC8C8C8FF)                                     # grey picture ...
        black = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 40, 6)
        black.fill(0x000000FF)
        black.copy_area(0, 0, 40, 6, pix, 0, 0)                  # ... with 6 black rows above and below
        black.copy_area(0, 0, 40, 6, pix, 0, 34)
        cut = tv_info.crop_bars(pix)
        self.assertEqual((cut.get_width(), cut.get_height()), (40, 28))

    def test_colours_come_from_the_gtk_theme_then_from_gnome(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "theme.css").write_text('@define-color window-bg-color #1a1b26;\n@define-color accent_bg_color #cfc9ff;\n'
                                       '@define-color card-bg-color mix(#000, white, 0.2);\n')
        (tmp / "gtk.css").write_text('@import url("theme.css");\n@define-color window-fg-color rgba(10, 20, 30, 0.5);\n')
        pal = tv_info.palette(tmp / "gtk.css", accent_name="purple")
        self.assertEqual([round(x * 255) for x in pal["bg"][:3]], [0x1a, 0x1b, 0x26])
        self.assertEqual([round(x * 255) for x in pal["accent"][:3]], [0xcf, 0xc9, 0xff])   # underscores in names are fine
        self.assertEqual(pal["fg"][3], 0.5)
        self.assertEqual([round(x * 255) for x in pal["card"][:3]], [0x30, 0x30, 0x30])      # mix() is not read: the default is used
        none = tv_info.palette(tmp / "missing.css", accent_name="teal", scheme="prefer-light")
        self.assertEqual([round(x * 255) for x in none["accent"][:3]], [0x21, 0x90, 0xa4])  # GNOME's accent colour
        self.assertGreater(none["bg"][0], 0.9)                                              # light scheme
        self.assertIsNone(tv_info.parse_color("@accent"))
        self.assertEqual(tv_info.parse_color("#fff"), (1.0, 1.0, 1.0, 1.0))

    def test_artwork_only_from_files_and_web_and_small(self):
        tmp = Path(tempfile.mkdtemp()) / "a.png"
        tmp.write_bytes(b"x" * 10)
        self.assertEqual(tv_info.fetch_art("file://" + str(tmp)), b"x" * 10)
        self.assertIsNone(tv_info.fetch_art("ftp://example.org/a.png"))
        self.assertIsNone(tv_info.fetch_art("file:///does/not/exist.png"))

    def test_only_device_batteries_are_listed(self):
        root = Path(tempfile.mkdtemp())
        for name, scope, cap, status in (("ps-controller-battery-d0:bc", "Device", "65", "Charging"),
                                         ("BAT0", "System", "90", "Discharging"), ("hid-1234-battery", "Device", "40", "Discharging")):
            d = root / name
            d.mkdir()
            (d / "type").write_text("Battery\n"); (d / "scope").write_text(scope + "\n")
            (d / "capacity").write_text(cap + "\n"); (d / "status").write_text(status + "\n")
        got = tv_info.batteries(root)
        self.assertEqual(len(got), 2)
        self.assertTrue(any(x.startswith("pad d0:bc 65%") and "charging" in x for x in got))
        self.assertFalse(any("90%" in x for x in got))


if __name__ == "__main__":
    unittest.main()
