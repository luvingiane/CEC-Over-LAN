#!/usr/bin/env python3
"""Writes Config.java: where the receiver is and what this TV looks like, for the app.

  gen_config.py <profile.json> <out.java>   with CEC_HOST, CEC_TOKEN (and CEC_PORT, CEC_ACTIVE_PATH, CEC_CONSUME)
  gen_config.py --check <profile.json>      only says whether a profile is usable

A TV profile says how the app can tell that the computer is on screen, because that differs between makers:
  hdmi_host_packages  packages that are in front while an HDMI input is shown (the TV's own "Live TV" app)
  tuner_markers       parts of a package name that mean the broadcast tuner is shown instead
  keep_keys           keys the TV always keeps (they are never consumed, so the TV can still be switched off)
android/profiles/ has the profiles that were tried on real TVs; docs/ADAPT-PROMPT.md is how a new one is made.
"""
import json
import os
import re
import sys

PACKAGE = re.compile(r"^[A-Za-z][\w]*(\.[A-Za-z0-9_]+)*$")
KEY = re.compile(r"^KEY_[A-Z0-9_]+$")


def check(profile):
    """The profile if it is usable, otherwise ValueError saying what is wrong."""
    if not isinstance(profile, dict):
        raise ValueError("the profile is a JSON object")
    hosts = profile.get("hdmi_host_packages")
    if not isinstance(hosts, list) or not hosts or not all(isinstance(h, str) and PACKAGE.match(h) for h in hosts):
        raise ValueError('"hdmi_host_packages" is a non-empty list of package names, e.g. ["com.example.livetv"]')
    markers = profile.get("tuner_markers", [])
    if not isinstance(markers, list) or not all(isinstance(m, str) and re.fullmatch(r"[\w.]{3,60}", m) for m in markers):
        raise ValueError('"tuner_markers" is a list of short package-name parts')
    keys = profile.get("keep_keys", ["KEY_POWER", "KEY_HOMEPAGE", "KEY_VOLUMEUP", "KEY_VOLUMEDOWN", "KEY_MUTE"])
    if not isinstance(keys, list) or not all(isinstance(k, str) and KEY.match(k) for k in keys):
        raise ValueError('"keep_keys" is a list of KEY_ names')
    return {"hosts": hosts, "markers": markers, "keys": keys}


def java(profile, host, token, port=8766, active_path="0x1000", consume=False):
    p = check(profile)
    s = json.dumps                                   # a JSON string is a valid Java string literal for these characters
    arr = lambda xs: "{" + ", ".join(s(x) for x in xs) + "}"
    if not re.fullmatch(r"[\w.:-]{1,253}", host) or not re.fullmatch(r"[\w.-]{8,200}", token):
        raise ValueError("odd host or token")
    return ("package org.cecoverlan.tv;\n\nfinal class Config {\n"
            f"    static final String HOST = {s(host)}, TOKEN = {s(token)}, ACTIVE_PATH = {s(active_path)};\n"
            f"    static final int PORT = {int(port)};\n"
            f"    static final boolean CONSUME = {'true' if consume else 'false'};\n"
            f"    static final String[] HDMI_HOSTS = {arr(p['hosts'])};\n"
            f"    static final String[] TUNER_MARKERS = {arr(p['markers'])};\n"
            f"    static final String[] KEEP_KEYS = {arr(p['keys'])};\n}}\n")


if __name__ == "__main__":
    if sys.argv[1] == "--check":
        try:
            check(json.load(open(sys.argv[2])))
        except (ValueError, OSError) as exc:
            sys.exit(f"{sys.argv[2]}: {exc}")
        print(f"{sys.argv[2]}: ok")
        sys.exit(0)
    profile_path, out = sys.argv[1:3]
    src = java(json.load(open(profile_path)), os.environ["CEC_HOST"], os.environ["CEC_TOKEN"],
               os.environ.get("CEC_PORT", "8766"), os.environ.get("CEC_ACTIVE_PATH", "0x1000"),
               os.environ.get("CEC_CONSUME", "false") == "true")
    open(out, "w").write(src)
