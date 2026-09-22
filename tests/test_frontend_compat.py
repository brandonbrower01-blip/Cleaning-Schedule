#!/usr/bin/env python3
"""Guard rails for the iOS 9 target.

The front end has to keep running on Safari 9, which is the newest browser a
1st generation iPad mini can install.  These tests fail if someone reaches for
something that browser does not have.
"""

import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "app.js")
CSS = os.path.join(ROOT, "static", "css", "style.css")
HTML = os.path.join(ROOT, "static", "index.html")


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


class JavaScriptTests(unittest.TestCase):

    def setUp(self):
        with open(JS, encoding="utf-8") as handle:
            self.code = strip_comments(handle.read())

    def test_no_es6_syntax(self):
        banned = {
            "arrow function": r"=>",
            "let binding": r"\blet\s+[A-Za-z_$]",
            "const binding": r"\bconst\s+[A-Za-z_$]",
            "template literal": r"`",
            "class declaration": r"\bclass\s+[A-Za-z_$]+\s*\{",
            "spread or rest": r"\.\.\.",
            "default parameter": r"function\s*[A-Za-z_$]*\s*\([^)]*=[^)]*\)",
        }
        for label, pattern in banned.items():
            found = re.search(pattern, self.code)
            self.assertIsNone(found, "%s is not available in Safari 9: %r" %
                              (label, found.group(0) if found else ""))

    def test_no_apis_missing_from_safari_9(self):
        banned = ["fetch(", "Promise", "Object.assign", "Array.from",
                  ".includes(", ".startsWith(", ".endsWith(", ".dataset"]
        for name in banned:
            self.assertNotIn(name, self.code, "%s is not available in Safari 9" % name)


class StylesheetTests(unittest.TestCase):

    def setUp(self):
        with open(CSS, encoding="utf-8") as handle:
            self.css = strip_comments(handle.read())

    def test_no_unsupported_css(self):
        banned = {
            "CSS grid": r"display:\s*grid",
            "custom property": r"--[a-z-]+\s*:",
            "var()": r"var\(",
            "position: sticky": r"position:\s*sticky",
            "gap": r"[^-a-z]gap:",
        }
        for label, pattern in banned.items():
            self.assertIsNone(re.search(pattern, self.css),
                              "%s does not work in Safari 9" % label)

    def test_flex_basis_always_carries_a_unit(self):
        """Safari 9 discards a flex shorthand whose basis is a unitless zero,
        which would leave those items sized to their content."""
        offenders = re.findall(r"flex:\s*\d+\s+\d+\s+0\s*;", self.css)
        self.assertEqual(offenders, [], "use 0%% instead: %r" % offenders)

    def test_flexbox_keeps_its_webkit_prefixes(self):
        self.assertIn("display: -webkit-flex", self.css)
        self.assertIn("-webkit-flex:", self.css)

    def test_touch_targets_stay_finger_sized(self):
        # the check box is the control that matters most on a tablet
        match = re.search(r"\.checkbox\s*\{[^}]*?width:\s*(\d+)px", self.css, re.S)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 40)


class MarkupTests(unittest.TestCase):

    def setUp(self):
        with open(HTML, encoding="utf-8") as handle:
            self.html = handle.read()

    def test_viewport_and_home_screen_tags(self):
        self.assertIn('name="viewport"', self.html)
        self.assertIn("apple-mobile-web-app-capable", self.html)
        self.assertIn("apple-touch-icon", self.html)

    def test_referenced_assets_exist(self):
        for path in re.findall(r'(?:href|src)="([^":]+)"', self.html):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", path)),
                            "missing asset: %s" % path)

    def test_no_external_resources(self):
        # the iPad may well be on a network with no internet at all
        self.assertNotIn("http://", self.html.replace("http://www.w3.org", ""))
        self.assertNotIn("https://", self.html)


if __name__ == "__main__":
    unittest.main()
