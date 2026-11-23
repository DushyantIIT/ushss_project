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

    def test_custom_sections_and_brand_settings_are_preserved(self):
        content = validate_homepage_content({
            "builder": {
                "site_title": "USHSS Updates",
                "theme": {"primary": "#123456"},
                "custom_sections": [{
                    "id": "admissions", "title": "Admissions",
                    "body": "Application information", "visible": True,
                    "button_label": "Apply", "button_url": "https://ipu.ac.in/"
                }]
            }
        })
        self.assertEqual(content["builder"]["site_title"], "USHSS Updates")
        self.assertEqual(content["builder"]["theme"]["primary"], "#123456")
        self.assertEqual(content["builder"]["custom_sections"][0]["id"], "admissions")

    def test_rejects_unsafe_custom_section_image(self):
        with self.assertRaises(ValueError):
            validate_homepage_content({"builder": {"custom_sections": [{
                "id": "unsafe-image", "title": "Unsafe", "image_url": "javascript:alert(1)"
            }]}})

    def test_rejects_duplicate_custom_section_ids(self):
        with self.assertRaises(ValueError):
            validate_homepage_content({"builder": {"custom_sections": [
                {"id": "updates", "title": "One"},
                {"id": "updates", "title": "Two"}
            ]}})


if __name__ == "__main__":
    unittest.main()
