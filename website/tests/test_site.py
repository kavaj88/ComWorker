from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


SITE_ROOT = Path(__file__).resolve().parents[1]
INDEX = SITE_ROOT / "index.html"


class SiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.html_lang = ""
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.resources: list[str] = []
        self.images: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if tag == "html":
            self.html_lang = values.get("lang", "")
        if values.get("id"):
            self.ids.add(values["id"])
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag == "link" and values.get("href"):
            self.resources.append(values["href"])
        if tag == "script" and values.get("src"):
            self.resources.append(values["src"])
        if tag == "img":
            self.images.append((values.get("src", ""), values.get("alt", "")))
        if tag == "meta":
            key = values.get("name") or values.get("property")
            if key:
                self.meta[key] = values.get("content", "")

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.text_parts.append(value)


def parse_site() -> SiteParser:
    parser = SiteParser()
    parser.feed(INDEX.read_text(encoding="utf-8"))
    return parser


def test_page_has_chinese_metadata_and_required_sections() -> None:
    parser = parse_site()
    assert parser.html_lang == "zh-CN"
    assert "width=device-width" in parser.meta["viewport"]
    assert "企业" in parser.meta["description"]
    assert {
        "pain",
        "solution",
        "capabilities",
        "scenarios",
        "deployment",
        "contact",
    } <= parser.ids


def test_copy_explains_enterprise_pain_and_solution() -> None:
    text = "".join(parse_site().text_parts)
    for keyword in ("账号", "知识", "权限", "成本"):
        assert keyword in text
    for keyword in ("统一治理", "独立工作空间", "用量", "审计"):
        assert keyword in text


def test_local_resources_and_images_are_complete() -> None:
    parser = parse_site()
    assert parser.images
    for source, alt in parser.images:
        assert source
        assert alt and any("\u4e00" <= char <= "\u9fff" for char in alt)
        assert (SITE_ROOT / source).is_file(), source

    for resource in parser.resources:
        parsed = urlparse(resource)
        assert not parsed.scheme and not parsed.netloc, resource
        assert (SITE_ROOT / resource).is_file(), resource


def test_navigation_anchors_resolve_to_page_sections() -> None:
    parser = parse_site()
    anchors = [link[1:] for link in parser.links if link.startswith("#") and len(link) > 1]
    assert anchors
    assert set(anchors) <= parser.ids


def test_site_does_not_depend_on_external_http_resources() -> None:
    html = INDEX.read_text(encoding="utf-8").lower()
    assert "src=\"http" not in html
    assert "href=\"http" not in html


def test_contact_section_uses_official_account_qrcode() -> None:
    parser = parse_site()
    text = "".join(parser.text_parts)
    assert "击水飞舟" in text
    assert "comworker" in text.lower()
    assert "免费获取安装包" in text
    assert "下载功能说明书" not in text
    assert not [link for link in parser.links if link.lower().endswith(".docx")]
    assert ("assets/qrcode.jpg", "击水飞舟公众号二维码") in parser.images


def test_reveal_animation_is_progressive_enhancement() -> None:
    html = INDEX.read_text(encoding="utf-8")
    css = (SITE_ROOT / "styles.css").read_text(encoding="utf-8")
    assert "document.documentElement.classList.add('js')" in html
    assert ".js .reveal" in css
