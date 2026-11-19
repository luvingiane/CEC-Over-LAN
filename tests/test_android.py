"""The scan code -> KEY_* table of the TV app must give the names the receiver's keymap uses."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "android"))
import gen_config  # noqa: E402
import gen_keynames  # noqa: E402

HEADER = Path("/usr/include/linux/input-event-codes.h")


@unittest.skipUnless(HEADER.exists(), "linux headers not installed")
class KeyNames(unittest.TestCase):
    def test_the_codes_seen_in_the_logs_keep_their_names(self):
        t = gen_keynames.table(HEADER.read_text())
        want = {166: "KEY_STOPCD", 161: "KEY_EJECTCD", 410: "KEY_SHUFFLE", 217: "KEY_SEARCH", 353: "KEY_SELECT",
                172: "KEY_HOMEPAGE", 158: "KEY_BACK", 174: "KEY_EXIT", 402: "KEY_CHANNELUP", 114: "KEY_VOLUMEDOWN"}
        for code, name in want.items():
            self.assertEqual(t.get(code), name, code)

    def test_only_plain_numbers_and_the_first_name_wins(self):
        t = gen_keynames.table("#define KEY_A 30\n#define KEY_B KEY_A\n#define KEY_C 0x1e /* same code */\n#define BTN_X 5\n")
        self.assertEqual(t, {30: "KEY_A"})

    def test_the_java_file_is_well_formed(self):
        j = gen_keynames.java({30: "KEY_A"})
        self.assertIn('30, "KEY_A"', j)
        self.assertIn("static String of(int scanCode)", j)


class TvProfile(unittest.TestCase):
    def test_the_profiles_in_the_repo_are_valid(self):
        import json
        for path in (Path(__file__).resolve().parent.parent / "android" / "profiles").glob("*.json"):
            gen_config.check(json.loads(path.read_text()))

    def test_the_config_carries_the_profile(self):
        src = gen_config.java({"hdmi_host_packages": ["com.example.livetv"], "tuner_markers": ["dvb"],
                               "keep_keys": ["KEY_POWER"]}, "192.0.2.7", "token-0123456789", consume=True)
        self.assertIn('HOST = "192.0.2.7"', src)
        self.assertIn('HDMI_HOSTS = {"com.example.livetv"}', src)
        self.assertIn('TUNER_MARKERS = {"dvb"}', src)
        self.assertIn('KEEP_KEYS = {"KEY_POWER"}', src)
        self.assertIn("CONSUME = true", src)

    def test_bad_profiles_are_refused_with_a_reason(self):
        for bad in ({}, {"hdmi_host_packages": []}, {"hdmi_host_packages": ["not a package"]},
                    {"hdmi_host_packages": ["a.b"], "tuner_markers": ["x\"; evil"]},
                    {"hdmi_host_packages": ["a.b"], "keep_keys": ["power"]}, []):
            with self.assertRaises(ValueError):
                gen_config.check(bad)
        with self.assertRaises(ValueError):
            gen_config.java({"hdmi_host_packages": ["a.b"]}, 'host"; x', "token-0123456789")


if __name__ == "__main__":
    unittest.main()
