"""Shared defaults and validation for the public homepage CMS."""
from copy import deepcopy

SECTION_IDS = ("home", "about", "programmes", "faculty", "gallery", "contact")
DEFAULT_HOMEPAGE_CONTENT = {
    "hero_eyebrow": "University School of Humanities & Social Sciences",
    "hero_title": "Where Ideas",
    "hero_accent": "Shape Society",
    "hero_subtitle": "A centre of academic excellence at GGSIPU, dedicated to the study of human culture, society, and thought — fostering critical minds and compassionate scholars.",
    "hero_primary_label": "Explore Programmes",
    "hero_primary_target": "#programmes",
    "hero_secondary_label": "Learn More",
    "hero_secondary_target": "#about",
    "hero_image_url": "",
    "about_label": "About USHSS",
    "about_title": "A Legacy of Intellectual Excellence",
    "about_body": "",
    "contact_heading": "We'd love to hear from you",
    "contact_body": "Whether you're a prospective student, researcher, or collaborating institution — our team is here to help.",
    "sections": {key: True for key in SECTION_IDS},
}
TEXT_LIMITS = {
    "hero_eyebrow": 180, "hero_title": 100, "hero_accent": 100, "hero_subtitle": 700,
    "hero_primary_label": 50, "hero_primary_target": 180, "hero_secondary_label": 50,
    "hero_secondary_target": 180, "hero_image_url": 1000, "about_label": 100,
    "about_title": 180, "about_body": 1800, "contact_heading": 180, "contact_body": 700,
}
def validate_homepage_content(value):
    if not isinstance(value, dict):
        raise ValueError("Homepage content must be an object.")
    result = deepcopy(DEFAULT_HOMEPAGE_CONTENT)
    for key, limit in TEXT_LIMITS.items():
        if key not in value:
            continue
        item = value[key]
        if not isinstance(item, str) or len(item) > limit:
            raise ValueError(f"{key} must be text no longer than {limit} characters.")
        if key.endswith("_target") and item and not (item.startswith("#") or item.startswith("https://") or item.startswith("/")):
            raise ValueError(f"{key} must be a local anchor, relative path, or HTTPS URL.")
        if key == "hero_image_url" and item and not item.startswith("https://"):
            raise ValueError("Hero image URL must use HTTPS.")
        result[key] = item
    sections = value.get("sections", result["sections"])
    if not isinstance(sections, dict) or set(sections) - set(SECTION_IDS):
        raise ValueError("Sections contains an unsupported section.")
    for key, visible in sections.items():
        if not isinstance(visible, bool):
            raise ValueError(f"Section visibility for {key} must be true or false.")
        result["sections"][key] = visible
    return result
