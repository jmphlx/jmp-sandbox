#!/usr/bin/env python3
"""Convert a JMP DITA source tree into staged AEM Edge Delivery content.

The converter starts at a root DITA map, follows its map hierarchy to the
topics it references, and renders each topic's XML body as plain HTML. It reads
the Oxygen source files directly; no WebHelp export is needed.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import posixpath
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from functools import lru_cache
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional, Union
from urllib.parse import quote, unquote, urlsplit


DEFAULT_MAP = "JMP_Documentation_Library.ditamap"
DEFAULT_ROBOTS = "noindex, follow"
TOPIC_SUFFIXES = {".xml", ".dita"}
MAP_SUFFIX = ".ditamap"
MAP_CONTAINERS = {
    "chapter",
    "appendix",
    "mapref",
    "part",
    "topicgroup",
    "topichead",
    "topicref",
    "topicset",
}
DROP_TAGS = {
    "alt",
    "critdates",
    "data",
    "draft-comment",
    "indexterm",
    "keywords",
    "metadata",
    "prolog",
    "related-links",
    "resourceid",
    "title",
    "topicmeta",
}
TRANSPARENT_TAGS = {
    "body",
    "dlentry",
    "entry",
    "fn",
    "keyword",
    "ph",
    "plentry",
    "row",
    "tbody",
    "term",
    "tgroup",
    "thead",
}
SECTION_TAGS = {"bodydiv", "div", "example", "section", "sectiondiv", "stepsection"}
PREFORMATTED_TAGS = {"codeblock", "lines", "pre", "screen"}
CODE_TAGS = {
    "apiname",
    "cmdname",
    "codeph",
    "filepath",
    "kwd",
    "markupname",
    "option",
    "parmname",
    "systemoutput",
    "userinput",
    "varname",
}
INLINE_TAGS = {
    "b": "strong",
    "cite": "em",
    "em": "em",
    "i": "em",
    "q": "q",
    "strong": "strong",
    "sub": "sub",
    "sup": "sup",
    "u": "u",
}
BLOCK_TAGS = {
    "dd": "dd",
    "dl": "dl",
    "dt": "dt",
    "li": "li",
    "ol": "ol",
    "p": "p",
    "parml": "dl",
    "pd": "dd",
    "pt": "dt",
    "sl": "ul",
    "sli": "li",
    "ul": "ul",
}
STRIPPED_BLOCK_TAGS = {"dd", "dt", "li", "p"}
BLOCK_CHILD_TAGS = {"codeblock", "dl", "example", "fig", "image", "lines", "note", "ol", "pre", "screen", "section", "simpletable", "table", "ul"}
NOTE_LABELS = {
    "attention": "Attention",
    "caution": "Caution",
    "danger": "Danger",
    "important": "Important",
    "note": "Note",
    "notice": "Notice",
    "remember": "Remember",
    "restriction": "Restriction",
    "tip": "Tip",
    "warning": "Warning",
}


@dataclass
class Key:
    text: str = ""
    href: str = ""


@dataclass
class Topic:
    relative: PurePosixPath
    root: ET.Element
    title: str
    description: str
    route_path: str = ""
    parent_route: str = ""
    parent_title: str = ""
    anchor: str = ""

    @property
    def route(self) -> str:
        return self.route_path or route_for(self.relative)

    @property
    def legacy_url(self) -> str:
        suffix = f"#{self.anchor}" if self.anchor else ""
        return "/" + self.relative.with_suffix(".html").as_posix() + suffix


def child_topics(parent: Topic) -> list[Topic]:
    """Nested <topic> elements become child pages at <parent route>/<child title>, at any depth."""
    children: list[Topic] = []

    def visit(node: ET.Element, owner: Topic) -> None:
        for child in node:
            if child.tag == "topic":
                title = text_of(child.find("title")) or child.get("id") or "section"
                page = Topic(
                    relative=parent.relative,
                    root=child,
                    title=title,
                    description=text_of(child.find("shortdesc")),
                    route_path=f"{owner.route}/{da_slug(title)}",
                    parent_route=owner.route,
                    parent_title=owner.title,
                    anchor=child.get("id") or "",
                )
                children.append(page)
                visit(child, page)
            else:
                visit(child, owner)

    visit(parent.root, parent)
    return children


@dataclass
class Issue:
    source: str
    kind: str
    value: str
    reason: str


class Documents:
    """Parses source XML once and caches roots and id indexes by relative path."""

    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root
        self._roots: dict[str, Optional[ET.Element]] = {}
        self._ids: dict[str, dict[str, ET.Element]] = {}

    def load(self, relative: PurePosixPath) -> Optional[ET.Element]:
        key = relative.as_posix()
        if key not in self._roots:
            path = self.source_root.joinpath(*relative.parts)
            root = None
            if path.is_file():
                try:
                    root = ET.parse(path).getroot()
                except ET.ParseError as error:
                    raise ValueError(f"Could not parse {key}: {error}") from error
            self._roots[key] = root
        return self._roots[key]

    def ids(self, relative: PurePosixPath) -> dict[str, ET.Element]:
        key = relative.as_posix()
        if key not in self._ids:
            root = self.load(relative)
            self._ids[key] = (
                {}
                if root is None
                else {node.get("id"): node for node in root.iter() if node.get("id")}
            )
        return self._ids[key]


@dataclass
class RenderContext:
    source_root: Path
    output_root: Path
    prefix: str
    topic_paths: set[str]
    documents: Documents
    keys: dict[str, Key]
    current: PurePosixPath
    issues: list[Issue]
    copied_assets: set[str]
    unhandled: Counter
    page_assets: set[str] = field(default_factory=set)
    child_by_node: dict = field(default_factory=dict)
    child_by_anchor: dict = field(default_factory=dict)
    embedded_bytes: int = 0
    page_links: int = 0
    heading_level: int = 1
    preformatted: int = 0
    suppress_ids: int = 0
    conref_depth: int = 0

    def issue(self, kind: str, value: str, reason: str) -> None:
        self.issues.append(Issue(self.current.as_posix(), kind, value, reason))


def text_of(node: Optional[ET.Element]) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def da_slug(name: str) -> str:
    """Lowercase a file or folder name and replace other characters with hyphens, as DA names documents."""
    return re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or "untitled"


def slug_path(relative: PurePosixPath) -> PurePosixPath:
    """Slug every folder part and the file stem of a topic path; the suffix is dropped by the caller."""
    return PurePosixPath(*(da_slug(part) for part in relative.parts))


def route_for(relative: PurePosixPath) -> str:
    return "/" + slug_path(relative.with_suffix("")).as_posix()


def normalized_prefix(value: str) -> str:
    parts = PurePosixPath(value.strip("/"))
    if not value.strip("/") or any(part in {".", ".."} for part in parts.parts):
        raise ValueError("The destination prefix must be a non-root path such as /help.")
    return "/" + parts.as_posix()


def safe_relative_path(path: str, base: PurePosixPath) -> Optional[PurePosixPath]:
    """Resolve a DITA href against the file that contains it, staying inside the source."""
    decoded = unquote(path)
    if decoded.startswith("/"):
        candidate = PurePosixPath(decoded.lstrip("/"))
    else:
        candidate = PurePosixPath(posixpath.normpath(posixpath.join(base.parent.as_posix(), decoded)))
    if candidate.is_absolute() or ".." in candidate.parts or candidate == PurePosixPath("."):
        return None
    return candidate


def is_remote(href: str) -> bool:
    parsed = urlsplit(href)
    return bool(parsed.scheme or parsed.netloc)


def collect_keys(map_root: ET.Element, keys: dict[str, Key]) -> None:
    for keydef in map_root.iter("keydef"):
        text = text_of(keydef.find(".//keyword")) or text_of(keydef.find(".//navtitle"))
        href = keydef.get("href") or ""
        for name in (keydef.get("keys") or "").split():
            keys.setdefault(name, Key(text=text, href=href))


def discover_topics(
    documents: Documents,
    map_relative: PurePosixPath,
    keys: dict[str, Key],
    issues: list[Issue],
) -> list[PurePosixPath]:
    """Return topic paths in map order, following mapref and ditamap topicrefs."""
    ordered: list[PurePosixPath] = []
    seen_topics: set[str] = set()
    seen_maps: set[str] = set()

    def visit_map(map_path: PurePosixPath) -> None:
        if map_path.as_posix() in seen_maps:
            return
        seen_maps.add(map_path.as_posix())
        map_root = documents.load(map_path)
        if map_root is None:
            issues.append(Issue(map_path.as_posix(), "missing-map", map_path.as_posix(), "Map file was not found."))
            return
        collect_keys(map_root, keys)
        walk(map_root, map_path)

    def walk(node: ET.Element, map_path: PurePosixPath) -> None:
        for child in node:
            if child.tag not in MAP_CONTAINERS:
                continue
            href = child.get("href") or ""
            if href and not is_remote(href) and urlsplit(href).path:
                target = safe_relative_path(urlsplit(href).path, map_path)
                if target is None:
                    issues.append(
                        Issue(map_path.as_posix(), "unsafe-path", href, "Map reference escapes the source folder.")
                    )
                elif target.suffix.lower() == MAP_SUFFIX:
                    visit_map(target)
                elif target.suffix.lower() in TOPIC_SUFFIXES and target.as_posix() not in seen_topics:
                    seen_topics.add(target.as_posix())
                    if documents.load(target) is None:
                        issues.append(
                            Issue(map_path.as_posix(), "missing-topic", href, "Topic referenced by the map was not found.")
                        )
                    else:
                        ordered.append(target)
            walk(child, map_path)

    visit_map(map_relative)
    return ordered


def category_for(topic: Topic) -> str:
    return topic.relative.parts[0] if len(topic.relative.parts) > 1 else "Other"


def copy_asset(relative: PurePosixPath, context: RenderContext, value: str) -> bool:
    source = context.source_root.joinpath(*relative.parts)
    if not source.is_file():
        context.issue("missing-asset", value, "Local asset was not found.")
        return False
    resolved = source.resolve()
    if not resolved.is_relative_to(context.source_root):
        context.issue("unsafe-asset", value, "Asset resolves outside the source folder.")
        return False

    target_relative = PurePosixPath(context.prefix.strip("/")) / relative
    target = context.output_root.joinpath(*target_relative.parts)
    if relative.as_posix() not in context.copied_assets:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(resolved, target)
        context.copied_assets.add(relative.as_posix())
    context.page_assets.add(relative.as_posix())
    return True


IMAGE_MIME_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


@lru_cache(maxsize=None)
def image_data_uri(path: str) -> str:
    """Base64 data URI for an image file, so the image lives inside the page that references it."""
    mime = IMAGE_MIME_TYPES[PurePosixPath(path).suffix.lower()]
    return f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode('ascii')}"


def embed_image(relative: PurePosixPath, context: RenderContext, value: str) -> Optional[str]:
    """Return a data URI for a local image referenced by the current page, or None if it cannot be embedded."""
    source = context.source_root.joinpath(*relative.parts)
    if not source.is_file():
        context.issue("missing-asset", value, "Local asset was not found.")
        return None
    resolved = source.resolve()
    if not resolved.is_relative_to(context.source_root):
        context.issue("unsafe-asset", value, "Asset resolves outside the source folder.")
        return None
    if relative.suffix.lower() not in IMAGE_MIME_TYPES:
        context.issue("unsupported-image", value, "Image type cannot be embedded; it was left out.")
        return None
    context.page_assets.add(relative.as_posix())
    context.embedded_bytes += resolved.stat().st_size
    return image_data_uri(str(resolved))


def image_source(href: str, context: RenderContext) -> Optional[str]:
    parsed = urlsplit(href)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https"}:
        return href
    if scheme == "data" and href.lower().startswith("data:image/"):
        return href
    if scheme or parsed.netloc:
        context.issue("unsafe-url", href, "Unsupported image URL was removed.")
        return None
    target = safe_relative_path(parsed.path, context.current)
    if target is None:
        context.issue("unsafe-path", href, "Reference escapes the source folder.")
        return None
    return embed_image(target, context, href)


def element_anchor(fragment: str) -> str:
    """DITA fragments are topicid or topicid/elementid; the last part names the target."""
    return fragment.split("/")[-1] if fragment else ""


def element_label(node: ET.Element, fallback: str) -> str:
    """Link text for an anchor-only xref: the target's title, else its opening text."""
    label = text_of(node.find("title")) or text_of(node)
    if len(label) > 80:
        label = label[:80].rsplit(" ", 1)[0] + "..."
    return label or fallback


def link_target(href: str, context: RenderContext) -> Optional[tuple[str, str]]:
    """Return (url, fallback link text) for an xref, or None to keep only its text."""
    parsed = urlsplit(href)
    scheme = parsed.scheme.lower()
    if scheme in {"http", "https", "mailto", "tel"} or parsed.netloc:
        return href, href
    if scheme:
        context.issue("unsafe-url", href, "Unsupported URL scheme was removed.")
        return None

    anchor = element_anchor(parsed.fragment)
    if not parsed.path:
        child = context.child_by_anchor.get((context.current.as_posix(), anchor)) if anchor else None
        if child is not None:
            return f"{context.prefix}{child.route}", child.title
        target_node = context.documents.ids(context.current).get(anchor)
        if target_node is not None:
            return f"#{anchor}", element_label(target_node, anchor)
        context.issue("unresolved-anchor", href, "Anchor was not found on this page.")
        return None

    target = safe_relative_path(parsed.path, context.current)
    if target is None:
        context.issue("unsafe-path", href, "Reference escapes the source folder.")
        return None

    if target.suffix.lower() in TOPIC_SUFFIXES | {MAP_SUFFIX}:
        if target.as_posix() not in context.topic_paths:
            context.issue(
                "unmapped-topic-link", href, "Target is not a converted topic; link text was kept without a link."
            )
            return None
        document = context.documents.load(target)
        root_id = document.get("id") if document is not None else None
        url = f"{context.prefix}{route_for(target)}"
        label = text_of(document.find("title")) if document is not None else ""
        child = context.child_by_anchor.get((target.as_posix(), anchor)) if anchor else None
        if child is not None:
            context.page_links += 1
            return f"{context.prefix}{child.route}", child.title
        if anchor and anchor != root_id:
            target_node = context.documents.ids(target).get(anchor)
            if target_node is not None:
                url += f"#{anchor}"
                label = element_label(target_node, anchor)
            else:
                context.issue("unresolved-anchor", href, "Anchor was not found in the target topic.")
        context.page_links += 1
        return url, label

    if copy_asset(target, context, href):
        return f"{context.prefix}/{target.as_posix()}", target.name
    return None


def id_attr(node: ET.Element, context: RenderContext) -> str:
    value = node.get("id")
    if context.suppress_ids or not value:
        return ""
    return f' id="{html.escape(value, quote=True)}"'


def text_html(context: RenderContext, text: Optional[str]) -> str:
    if not text:
        return ""
    if context.preformatted:
        return html.escape(text, quote=False)
    return html.escape(re.sub(r"\s+", " ", text), quote=False)


def render_inline(node: ET.Element, context: RenderContext) -> str:
    parts = [text_html(context, node.text)]
    for child in node:
        parts.append(render_element(child, context))
        parts.append(text_html(context, child.tail))
    return "".join(parts)


def render_element(node: ET.Element, context: RenderContext) -> str:
    tag = node.tag
    if tag in DROP_TAGS:
        return ""
    if "conref" in node.attrib:
        return render_conref(node, context)
    if "keyref" in node.attrib and tag in {"ph", "keyword"}:
        return render_keyref(node, context)
    if tag == "xref":
        return render_xref(node, context)
    if tag == "image":
        return render_image(node, context)
    if tag == "fig":
        return render_fig(node, context)
    if tag == "note":
        return render_note(node, context)
    if tag in PREFORMATTED_TAGS:
        return render_preformatted(node, context)
    if tag == "table":
        return render_table(node, context)
    if tag == "topic" and id(node) in context.child_by_node:
        child = context.child_by_node[id(node)]
        return f'<p><a href="{context.prefix}{child.route}">{html.escape(child.title)}</a></p>'
    if tag in SECTION_TAGS or tag == "topic":
        return render_section(node, context)
    if tag == "shortdesc":
        return f"<p>{render_inline(node, context).strip()}</p>"
    if tag == "menucascade":
        return " &gt; ".join(render_element(child, context) for child in node)
    if tag == "uicontrol":
        return f"<strong>{render_inline(node, context)}</strong>"
    if tag == "foreign":
        return f"<code>{render_inline(node, context)}</code>"
    if tag in CODE_TAGS:
        return f"<code>{render_inline(node, context)}</code>"
    if tag in BLOCK_TAGS:
        content = render_inline(node, context)
        if tag in STRIPPED_BLOCK_TAGS:
            content = content.strip()
        wrapper = BLOCK_TAGS[tag]
        if tag == "p" and any(child.tag in BLOCK_CHILD_TAGS for child in node):
            # DITA allows a table or list inside p; HTML does not, so use a div for the container.
            wrapper = "div"
        return f"<{wrapper}{id_attr(node, context)}>{content}</{wrapper}>"
    if tag in INLINE_TAGS:
        wrapper = INLINE_TAGS[tag]
        return f"<{wrapper}>{render_inline(node, context)}</{wrapper}>"
    if tag not in TRANSPARENT_TAGS:
        context.unhandled[tag] += 1
    return render_inline(node, context)


def render_conref(node: ET.Element, context: RenderContext) -> str:
    """Render the element a conref points to, in place, using its own source file."""
    href = node.get("conref") or ""
    parsed = urlsplit(href)
    target_file = safe_relative_path(parsed.path, context.current) if parsed.path else context.current
    if target_file is None:
        context.issue("unsafe-path", href, "Conref escapes the source folder.")
        return ""
    document = context.documents.load(target_file)
    if document is None:
        context.issue("missing-conref", href, "Conref target file was not found.")
        return ""
    anchor = element_anchor(parsed.fragment)
    target = document if not anchor else context.documents.ids(target_file).get(anchor)
    if target is None:
        context.issue("unresolved-conref", href, "Conref target element was not found.")
        return ""
    if context.conref_depth >= 8:
        context.issue("conref-depth", href, "Conref nesting is too deep; content was skipped.")
        return ""

    previous = (context.current, context.suppress_ids)
    context.current = target_file
    context.suppress_ids += 1
    context.conref_depth += 1
    try:
        return render_element(target, context)
    finally:
        context.current, context.suppress_ids = previous
        context.conref_depth -= 1


def render_keyref(node: ET.Element, context: RenderContext) -> str:
    name = node.get("keyref") or ""
    key = context.keys.get(name)
    if key and key.text:
        return html.escape(key.text, quote=False)
    context.issue("unresolved-keyref", name, "Key has no text value; rendered the element content instead.")
    return render_inline(node, context)


def render_xref(node: ET.Element, context: RenderContext) -> str:
    href = node.get("href") or ""
    keyref = node.get("keyref") or ""
    if keyref and not href:
        key = context.keys.get(keyref)
        if key is None:
            context.issue("unresolved-keyref", keyref, "Key is not defined in the map; link text was kept.")
        elif key.href:
            href = key.href
        elif key.text:
            return html.escape(key.text, quote=False)

    text = render_inline(node, context).strip()
    if not href:
        return text
    link = link_target(href, context)
    if link is None:
        return text
    url, fallback = link
    if not text:
        text = html.escape(fallback or url, quote=False)
    return f'<a href="{html.escape(url, quote=True)}">{text}</a>'


def render_image(node: ET.Element, context: RenderContext) -> str:
    href = node.get("href") or ""
    alt = text_of(node.find("alt"))
    if not href:
        if node.get("keyref"):
            context.issue("unresolved-keyref", node.get("keyref") or "", "Image key is not supported.")
        return ""
    if not alt:
        context.issue("missing-alt", href, "Image has no alternative text; add it during content review.")
    src = image_source(href, context)
    if src is None:
        return ""
    return f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}">'


def render_fig(node: ET.Element, context: RenderContext) -> str:
    title = node.find("title")
    caption = ""
    if title is not None and text_of(title):
        caption = f"<figcaption>{render_inline(title, context).strip()}</figcaption>"
    return f"<figure{id_attr(node, context)}>{caption}{render_inline(node, context)}</figure>"


def render_note(node: ET.Element, context: RenderContext) -> str:
    note_type = (node.get("type") or "note").lower()
    label = NOTE_LABELS.get(note_type, "Note") + ":"
    body = render_inline(node, context).strip()
    return f"<blockquote><p><strong>{html.escape(label)}</strong></p>{body}</blockquote>"


def render_preformatted(node: ET.Element, context: RenderContext) -> str:
    context.preformatted += 1
    try:
        code = render_inline(node, context).strip("\n")
    finally:
        context.preformatted -= 1
    languages = [token for token in (node.get("outputclass") or "").split() if token.startswith("language-")]
    css = f' class="{html.escape(" ".join(languages), quote=True)}"' if languages else ""
    return f"<pre{css}><code>{code}</code></pre>"


def render_section(node: ET.Element, context: RenderContext) -> str:
    title = node.find("title")
    previous = context.heading_level
    heading = ""
    if title is not None and text_of(title):
        level = min(previous + 1, 6)
        heading = f"<h{level}>{render_inline(title, context).strip()}</h{level}>"
        context.heading_level = level
    try:
        body = render_inline(node, context)
    finally:
        context.heading_level = previous
    return f"<div{id_attr(node, context)}>{heading}{body}</div>"


def render_table(node: ET.Element, context: RenderContext) -> str:
    parts = [f"<table{id_attr(node, context)}>"]
    title = node.find("title")
    if title is not None and text_of(title):
        parts.append(f"<caption>{render_inline(title, context).strip()}</caption>")
    for group in node.findall("tgroup") or [node]:
        columns = {}
        for index, spec in enumerate(group.findall("colspec"), start=1):
            name = spec.get("colname")
            if name:
                columns[name] = int(spec.get("colnum") or index)
        head = group.find("thead")
        if head is not None:
            rows = "".join(render_row(row, "th", columns, context) for row in head.findall("row"))
            parts.append(f"<thead>{rows}</thead>")
        body = group.find("tbody")
        if body is not None:
            rows = "".join(render_row(row, "td", columns, context) for row in body.findall("row"))
            parts.append(f"<tbody>{rows}</tbody>")
    parts.append("</table>")
    return "".join(parts)


def render_row(row: ET.Element, cell_tag: str, columns: dict[str, int], context: RenderContext) -> str:
    cells = []
    for entry in row.findall("entry"):
        attributes = ""
        start = columns.get(entry.get("namest") or "")
        end = columns.get(entry.get("nameend") or "")
        if start and end and end > start:
            attributes += f' colspan="{end - start + 1}"'
        morerows = entry.get("morerows")
        if morerows and morerows.isdigit() and int(morerows) > 0:
            attributes += f' rowspan="{int(morerows) + 1}"'
        content = render_inline(entry, context).strip()
        cells.append(f"<{cell_tag}{attributes}>{content}</{cell_tag}>")
    return f"<tr>{''.join(cells)}</tr>"


def metadata_section(title: str, description: str, robots: str) -> str:
    rows = []
    for key, value in (("title", title), ("description", description), ("robots", robots)):
        if value:
            rows.append(
                "<div><div>{}</div><div>{}</div></div>".format(
                    html.escape(key), html.escape(value)
                )
            )
    if not rows:
        return ""
    return '<div><div class="metadata">{}</div></div>\n'.format("".join(rows))


def page_markup(topic: Topic, context: RenderContext, robots: str) -> str:
    category = category_for(topic)
    category_url = f"{context.prefix}/catalog/{da_slug(category)}"
    home_url = f"{context.prefix}/index"
    body = render_inline(topic.root, context)
    crumbs = (
        f'<a href="{home_url}">JMP Help</a> / <a href="{category_url}">{html.escape(category)}</a>'
    )
    if topic.parent_route:
        crumbs += f' / <a href="{context.prefix}{topic.parent_route}">{html.escape(topic.parent_title)}</a>'
    return (
        "<div>"
        f"<p>{crumbs}</p>"
        f"<h1>{html.escape(topic.title)}</h1>"
        f"{body}"
        "</div>\n"
        + metadata_section(topic.title, topic.description, robots)
    )


def index_markup(topics: list[Topic], prefix: str) -> tuple[str, dict[str, str]]:
    grouped: dict[str, list[Topic]] = {}
    for topic in topics:
        if topic.parent_route:
            continue
        grouped.setdefault(category_for(topic), []).append(topic)

    home = [
        "<div>",
        "<h1>JMP Help</h1>",
        "<p>Browse the guides or search the site for a topic.</p>",
        '<div class="search"><div><div><a href="/query-index.json">Search index</a></div></div></div>',
        "<h2>Guides</h2>",
        "<ul>",
    ]
    catalogs = {}
    for category, pages in sorted(grouped.items()):
        category_url = f"{prefix}/catalog/{da_slug(category)}"
        home.append(
            f'<li><a href="{category_url}">{html.escape(category)}</a> '
            f"({len(pages)} topics)</li>"
        )
        catalog = [
            "<div>",
            f'<p><a href="{prefix}/index">JMP Help</a></p>',
            f"<h1>{html.escape(category)}</h1>",
            "<ul>",
        ]
        for page in sorted(pages, key=lambda item: (item.title.casefold(), item.route)):
            catalog.append(
                f'<li><a href="{prefix}{page.route}">{html.escape(page.title)}</a></li>'
            )
        catalog.extend(["</ul>", "</div>\n"])
        catalogs[category] = "".join(catalog) + metadata_section(
            f"JMP Help - {category}", "", ""
        )
    home.extend(["</ul>", "</div>\n"])
    return "".join(home) + metadata_section("JMP Help", "", ""), catalogs


def as_document(main_markup: str) -> str:
    """Wrap section markup in the page structure DA stores for every document."""
    return f"<body>\n<header></header>\n<main>{main_markup}</main>\n<footer></footer>\n</body>\n"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def book_map_for(documents: Documents, root_relative: PurePosixPath, book: str) -> PurePosixPath:
    """Resolve --book to a book map: a .ditamap path under the source, or a book folder named in the root map."""
    if book.lower().endswith(MAP_SUFFIX):
        candidate = PurePosixPath(book)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"Book map must stay inside the source folder: {book}")
        if documents.load(candidate) is None:
            raise ValueError(f"Book map not found under the source folder: {book}")
        return candidate

    root = documents.load(root_relative)
    available = {}
    for mapref in root.iter("mapref") if root is not None else []:
        href = mapref.get("href") or ""
        parts = PurePosixPath(href).parts
        if len(parts) > 1 and not is_remote(href):
            available.setdefault(parts[0], []).append(PurePosixPath(href))
    matches = [path for folder, paths in available.items() if folder.casefold() == book.casefold() for path in paths]
    if not matches:
        names = ", ".join(sorted(available, key=str.casefold))
        raise ValueError(f"No book named {book!r} in {root_relative}. Available books: {names}")
    if len(matches) > 1:
        options = ", ".join(path.as_posix() for path in matches)
        raise ValueError(f"Book {book!r} has several maps; pass one with --book: {options}")
    return matches[0]


def convert(
    source: Path,
    output: Path,
    prefix: str = "/help",
    limit: Optional[int] = None,
    force: bool = False,
    map_name: str = DEFAULT_MAP,
    robots: str = DEFAULT_ROBOTS,
    book: Optional[str] = None,
) -> dict[str, Union[int, str]]:
    source_root = source.resolve()
    output_root = output.resolve()
    if not source_root.is_dir():
        raise ValueError(f"Source folder does not exist: {source_root}")
    if source_root == output_root or source_root.is_relative_to(output_root) or output_root.is_relative_to(source_root):
        raise ValueError("Source and output folders must not contain one another.")
    prefix = normalized_prefix(prefix)
    if limit is not None and limit < 1:
        raise ValueError("--limit must be greater than zero.")
    if output_root.exists() and any(output_root.iterdir()) and not force:
        raise ValueError(f"Output folder is not empty: {output_root}. Use --force to overwrite generated files.")
    map_relative = PurePosixPath(map_name)
    if map_relative.is_absolute() or ".." in map_relative.parts or not source_root.joinpath(*map_relative.parts).is_file():
        raise ValueError(f"Root map not found under the source folder: {map_name}")

    documents = Documents(source_root)
    keys: dict[str, Key] = {}
    issues: list[Issue] = []
    discovered = discover_topics(documents, map_relative, keys, issues)
    if book:
        book_map = book_map_for(documents, map_relative, book)
        in_book = set(discover_topics(documents, book_map, {}, []))
        discovered = [path for path in discovered if path in in_book]
    if not discovered:
        raise ValueError(f"No DITA topics are referenced by {book or map_name} under: {source_root}")
    selected = discovered[:limit] if limit is not None else discovered
    topics = [Topic(relative=path, root=documents.load(path), title="", description="") for path in selected]
    for topic in topics:
        topic.title = text_of(topic.root.find("title")) or topic.relative.stem.replace("_", " ")
        topic.description = text_of(topic.root.find("shortdesc"))
    children = [child for topic in topics for child in child_topics(topic)]
    child_by_node = {id(child.root): child for child in children}
    child_by_anchor: dict[tuple[str, str], Topic] = {}
    for child in children:
        if child.anchor:
            child_by_anchor.setdefault((child.relative.as_posix(), child.anchor), child)
    topics = topics + children

    topic_paths = {topic.relative.as_posix() for topic in topics}
    routes = Counter(topic.route for topic in topics)
    clashes = sorted(route for route, count in routes.items() if count > 1)
    if clashes:
        raise ValueError(f"Lowercase DA names collide for: {', '.join(clashes)}. Rename the source topics.")
    copied_assets: set[str] = set()
    unhandled: Counter = Counter()
    manifest_rows = []
    legacy_rows = []
    for topic in topics:
        context = RenderContext(
            source_root=source_root,
            output_root=output_root,
            prefix=prefix,
            topic_paths=topic_paths,
            documents=documents,
            keys=keys,
            current=topic.relative,
            issues=issues,
            copied_assets=copied_assets,
            unhandled=unhandled,
            child_by_node=child_by_node,
            child_by_anchor=child_by_anchor,
        )
        target_relative = PurePosixPath(prefix.strip("/")) / f"{topic.route.lstrip('/')}.html"
        target = output_root.joinpath(*target_relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(as_document(page_markup(topic, context, robots)), encoding="utf-8")
        manifest_rows.append(
            {
                "source": topic.relative.as_posix(),
                "output": "/" + target_relative.as_posix(),
                "legacy_url": topic.legacy_url,
                "title": topic.title,
                "description": topic.description,
                "assets": str(len(context.page_assets)),
                "internal_links": str(context.page_links),
            }
        )
        legacy_rows.append(
            {
                "source": topic.legacy_url,
                "destination": f"{prefix}{topic.route}",
                "title": topic.title,
            }
        )

    landing, catalogs = index_markup(topics, prefix)
    landing_path = output_root.joinpath(*PurePosixPath(prefix.strip("/")).parts, "index.html")
    landing_path.parent.mkdir(parents=True, exist_ok=True)
    landing_path.write_text(as_document(landing), encoding="utf-8")
    for category, markup in catalogs.items():
        catalog_path = (
            output_root.joinpath(*PurePosixPath(prefix.strip("/")).parts, "catalog")
            / f"{da_slug(category)}.html"
        )
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(as_document(markup), encoding="utf-8")

    reports = output_root / "_migration"
    write_csv(
        reports / "manifest.csv",
        ["source", "output", "legacy_url", "title", "description", "assets", "internal_links"],
        manifest_rows,
    )
    write_csv(
        reports / "legacy-url-map.csv",
        ["source", "destination", "title"],
        legacy_rows,
    )
    write_csv(
        reports / "unresolved-links.csv",
        ["source", "kind", "value", "reason"],
        [issue.__dict__ for issue in issues if issue.kind != "missing-alt"],
    )
    write_csv(
        reports / "accessibility.csv",
        ["source", "kind", "value", "reason"],
        [issue.__dict__ for issue in issues if issue.kind == "missing-alt"],
    )
    write_csv(
        reports / "unhandled-elements.csv",
        ["element", "occurrences"],
        [{"element": name, "occurrences": str(count)} for name, count in sorted(unhandled.items())],
    )

    all_topic_files = sum(
        1 for path in source_root.rglob("*") if path.is_file() and path.suffix.lower() in TOPIC_SUFFIXES
    )
    unresolved_references = sum(issue.kind != "missing-alt" for issue in issues)
    missing_alt_text = sum(issue.kind == "missing-alt" for issue in issues)
    summary: dict[str, Union[int, str]] = {
        "source": str(source_root),
        "output": str(output_root),
        "prefix": prefix,
        "rootMap": map_name,
        "topicPages": len(topics),
        "topicsInMap": len(discovered),
        "xmlFilesNotInMap": all_topic_files - len(discovered),
        "copiedAssets": len(copied_assets),
        "unresolvedReferences": unresolved_references,
        "missingAltText": missing_alt_text,
        "unhandledElements": sum(unhandled.values()),
        "generatedCatalogs": len(catalogs),
    }
    (reports / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage a JMP DITA source tree as AEM Edge Delivery plain HTML."
    )
    parser.add_argument("--source", required=True, type=Path, help="DITA source folder (contains the root map).")
    parser.add_argument("--output", required=True, type=Path, help="Empty staging folder outside the source.")
    parser.add_argument("--map", default=DEFAULT_MAP, help=f"Root DITA map, relative to --source (default: {DEFAULT_MAP}).")
    parser.add_argument("--prefix", default="/help", help="URL prefix for generated content (default: /help).")
    parser.add_argument(
        "--robots",
        default=DEFAULT_ROBOTS,
        help=f"Robots metadata for generated pages (default: '{DEFAULT_ROBOTS}'; the DITA source has none).",
    )
    parser.add_argument(
        "--book",
        help="Convert only one book: a folder named in the root map (for example UG) or a book .ditamap path.",
    )
    parser.add_argument("--limit", type=int, help="Convert only the first N topics for a smoke test.")
    parser.add_argument("--force", action="store_true", help="Overwrite generated files in a non-empty output folder.")
    args = parser.parse_args()

    try:
        summary = convert(
            args.source, args.output, args.prefix, args.limit, args.force, args.map, args.robots, args.book
        )
    except (OSError, ValueError) as error:
        print(f"Conversion failed: {error}", file=sys.stderr)
        return 1

    print(
        "Converted {topicPages} topics ({topicsInMap} in map), "
        "copied {copiedAssets} assets, and found {unresolvedReferences} "
        "references plus {missingAltText} image alt-text issues to review.".format(**summary)
    )
    print(f"Preview page: {summary['prefix']}/index")
    print(f"Reports: {Path(str(summary['output'])) / '_migration'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
