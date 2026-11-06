"""ctypes layouts must match 64-bit Windows exactly (wrong sizes = SendInput silently fails)."""

import ctypes
import struct

from assistant.win.inputs import INPUT, KEYBDINPUT, MOUSEINPUT, mouse_input, text_to_inputs


def test_struct_sizes_match_win64():
    assert struct.calcsize("P") == 8
    assert ctypes.sizeof(MOUSEINPUT) == 32
    assert ctypes.sizeof(KEYBDINPUT) == 24
    assert ctypes.sizeof(INPUT) == 40
    assert INPUT.u.offset == 8


def test_text_to_inputs_unicode_and_keys():
    ev = text_to_inputs("aü\n😀")
    # a, ü: 2 events each; newline: Enter down/up; emoji: surrogate pair -> 2 code units x 2
    assert len(ev) == 2 + 2 + 2 + 4
    assert ev[0].ki.wScan == ord("a") and ev[0].ki.dwFlags == 0x4 and ev[1].ki.dwFlags == 0x6
    assert ev[4].ki.wVk == 0x0D and ev[4].ki.wScan == 0
    assert (ev[6].ki.wScan, ev[8].ki.wScan) == (0xD83D, 0xDE00)


def test_negative_wheel_is_twos_complement():
    assert mouse_input(0x0800, -3 * 120).mi.mouseData == (2**32 - 360)
