import unittest

from stackchan_server.stackchan.lyrics import parse_srt

SAMPLE = """1
00:00:07,933 --> 00:00:09,200
一块屏幕一颗头

2
00:00:09,200 --> 00:00:10,366
我照样封王
"""


class ParseSrtTest(unittest.TestCase):
    def test_parses_cues_in_order(self):
        cues = parse_srt(SAMPLE)
        self.assertEqual(len(cues), 2)
        self.assertEqual([c["index"] for c in cues], [0, 1])
        self.assertEqual(cues[0]["text"], "一块屏幕一颗头")

    def test_timestamps_to_seconds(self):
        cues = parse_srt(SAMPLE)
        self.assertAlmostEqual(cues[0]["start"], 7.933)
        self.assertAlmostEqual(cues[0]["end"], 9.2)

    def test_tolerates_bom_crlf_and_trailing_space(self):
        text = "﻿1\r\n00:00:01,000 --> 00:00:02,000\r\n电流当心跳在响 \r\n"
        cues = parse_srt(text)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0]["text"], "电流当心跳在响")

    def test_joins_multiline_text(self):
        text = "1\n00:00:01,000 --> 00:00:02,000\nline one\nline two\n"
        cues = parse_srt(text)
        self.assertEqual(cues[0]["text"], "line one line two")

    def test_empty_input_yields_no_cues(self):
        self.assertEqual(parse_srt("   \n\n  "), [])


if __name__ == "__main__":
    unittest.main()
