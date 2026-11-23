"""Shared defaults and validation for the admin-managed public website."""
from copy import deepcopy
import re

SECTION_IDS = ("home", "about", "programmes", "faculty", "gallery", "contact")
DEFAULT_HOMEPAGE_CONTENT = {
    "hero_eyebrow": "University School of Humanities & Social Sciences",
    "hero_title": "Where Ideas", "hero_accent": "Shape Society",
    "hero_subtitle": "A centre of academic excellence at GGSIPU, dedicated to the study of human culture, society, and thought — fostering critical minds and compassionate scholars.",
    "hero_primary_label": "Explore Programmes", "hero_primary_target": "#programmes",
    "hero_secondary_label": "Learn More", "hero_secondary_target": "#about", "hero_image_url": "",
    "about_label": "About USHSS", "about_title": "A Legacy of Intellectual Excellence", "about_body": "",
    "contact_heading": "We'd love to hear from you",
    "contact_body": "Whether you're a prospective student, researcher, or collaborating institution — our team is here to help.",
    "sections": {key: True for key in SECTION_IDS},
    "builder": {
        "site_title": "USHSS, GGSIPU", "tagline": "University School of Humanities & Social Sciences",
        "theme": {"primary": "#881818", "accent": "#D89E28", "background": "#FCFAF6", "text": "#160F08"},
        "nav_links": [
            {"label": "About", "url": "#about"}, {"label": "Programmes", "url": "#programmes"},
            {"label": "Faculty", "url": "#faculty"}, {"label": "Contact", "url": "#contact"}
        ],
        "footer_heading": "University School of Humanities & Social Sciences",
        "footer_text": "Guru Gobind Singh Indraprastha University, New Delhi",
        "footer_links": [{"label": "GGSIPU Official Site", "url": "https://ipu.ac.in/"}],
        "custom_sections": []
    }
}
TEXT_LIMITS = {
    "hero_eyebrow": 180, "hero_title": 100, "hero_accent": 100, "hero_subtitle": 700,
    "hero_primary_label": 50, "hero_primary_target": 180, "hero_secondary_label": 50,
    "hero_secondary_target": 180, "hero_image_url": 1000, "about_label": 100,
    "about_title": 180, "about_body": 1800, "contact_heading": 180, "contact_body": 700,
}

def _text(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{name} must be text" + (f" (maximum {limit} characters)." if limit else "."))
    return value.strip()

def _url(value, name, allow_anchor=True):
    value = _text(value, name, 1000)
    if not value:
        return value
    if allow_anchor and re.fullmatch(r"#[A-Za-z][A-Za-z0-9_-]{0,79}", value):
        return value
    if value.startswith("/") and not value.startswith("//"):
        return value
    if value.startswith("https://"):
        return value
    raise ValueError(f"{name} must be an anchor, relative path, or HTTPS URL.")

def validate_homepage_content(value):
    if not isinstance(value, dict):
        raise ValueError("Website content must be an object.")
    result = deepcopy(DEFAULT_HOMEPAGE_CONTENT)
    for key, limit in TEXT_LIMITS.items():
        if key not in value:
            continue
        item = _text(value[key], key, limit)
        if key.endswith("_target"):
            item = _url(item, key)
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

    raw = value.get("builder", {})
    if not isinstance(raw, dict):
        raise ValueError("Builder settings must be an object.")
    builder = result["builder"]
    for key, limit in (("site_title", 100), ("tagline", 180), ("footer_heading", 180), ("footer_text", 1000)):
        if key in raw:
            builder[key] = _text(raw[key], key, limit)
    theme = raw.get("theme", {})
    if not isinstance(theme, dict):
        raise ValueError("Theme settings must be an object.")
    for key in ("primary", "accent", "background", "text"):
        candidate = theme.get(key, builder["theme"][key])
        if not isinstance(candidate, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", candidate):
            raise ValueError(f"Theme {key} must be a six-digit hex colour.")
        builder["theme"][key] = candidate.upper()

    for field in ("nav_links", "footer_links"):
        links = raw.get(field, builder[field])
        if not isinstance(links, list) or len(links) > 12:
            raise ValueError(f"{field} must be a list of at most 12 links.")
        clean = []
        for item in links:
            if not isinstance(item, dict):
                raise ValueError(f"Each {field} item must be an object.")
            clean.append({"label": _text(item.get("label", ""), "link label", 60, True),
                          "url": _url(item.get("url", ""), "link URL")})
        builder[field] = clean

    custom = raw.get("custom_sections", builder["custom_sections"])
    if not isinstance(custom, list) or len(custom) > 15:
        raise ValueError("You can create up to 15 custom homepage sections.")
    clean_sections, seen = [], set(SECTION_IDS)
    for index, section in enumerate(custom):
        if not isinstance(section, dict):
            raise ValueError("Each custom section must be an object.")
        sid = _text(section.get("id", f"custom-{index+1}"), "section id", 48, True)
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,47}", sid) or sid in seen:
            raise ValueError("Custom section IDs must be unique lowercase slugs.")
        seen.add(sid)
        image = _text(section.get("image_url", ""), "section image URL", 1000)
        if image and not image.startswith("https://"):
            raise ValueError("Section images must use HTTPS.")
        visible = section.get("visible", True)
        if not isinstance(visible, bool):
            raise ValueError("Section visibility must be true or false.")
        clean_sections.append({
            "id": sid, "eyebrow": _text(section.get("eyebrow", ""), "section eyebrow", 100),
            "title": _text(section.get("title", "New section"), "section title", 180, True),
            "body": _text(section.get("body", ""), "section body", 3000),
            "image_url": image, "button_label": _text(section.get("button_label", ""), "button label", 60),
            "button_url": _url(section.get("button_url", ""), "button URL"), "visible": visible
        })
    builder["custom_sections"] = clean_sections
    result["builder"] = builder
    return result
