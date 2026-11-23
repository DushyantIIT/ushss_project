import unittest

from app.homepage_content import DEFAULT_HOMEPAGE_CONTENT, validate_homepage_content


class HomepageContentTests(unittest.TestCase):
    def test_defaults_are_complete(self):
        content = validate_homepage_content({})
        self.assertEqual(content["hero_title"], DEFAULT_HOMEPAGE_CONTENT["hero_title"])
        self.assertTrue(content["sections"]["home"])

    def test_valid_edits_are_preserved(self):
        content = validate_homepage_content({
            "hero_title": "Learning and Inquiry",
            "sections": {"gallery": False},
        })
        self.assertEqual(content["hero_title"], "Learning and Inquiry")
        self.assertFalse(content["sections"]["gallery"])
        self.assertTrue(content["sections"]["contact"])

    def test_rejects_oversized_text(self):
        with self.assertRaises(ValueError):
            validate_homepage_content({"hero_title": "x" * 101})

    def test_rejects_unsafe_image_url(self):
        with self.assertRaises(ValueError):
            validate_homepage_content({"hero_image_url": "http://example.com/image.jpg"})

    def test_rejects_unknown_section(self):
        with self.assertRaises(ValueError):
            validate_homepage_content({"sections": {"admin": True}})


if __name__ == "__main__":
    unittest.main()
