#!/usr/bin/env python3
"""List EPUB navigation entries and extract selected sections as JSON."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


BLOCK_TAGS = {
    "address",
    "blockquote",
    "caption",
    "dd",
    "dt",
    "figcaption",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "pre",
}
HEADING_LEVELS = {f"h{level}": level for level in range(1, 7)}
SKIP_TAGS = {"head", "script", "style", "svg"}
EPUB_NS = "http://www.idpf.org/2007/ops"


@dataclass
class TocEntry:
    index: int
    title: str
    href: str
    depth: int


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def normalized_text(element: ET.Element) -> str:
    return re.sub(r"\s+", " ", " ".join(element.itertext())).strip()


def member_path(base_dir: str, href: str) -> tuple[str, str]:
    parts = urlsplit(href)
    decoded_path = unquote(parts.path)
    full_path = posixpath.normpath(posixpath.join(base_dir, decoded_path))
    return full_path, unquote(parts.fragment)


def parse_xml(data: bytes, label: str) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"无法解析 EPUB 内的 XML/XHTML（{label}）：{exc}") from exc


def container_package_path(archive: zipfile.ZipFile) -> str:
    try:
        root = parse_xml(archive.read("META-INF/container.xml"), "META-INF/container.xml")
    except KeyError as exc:
        raise ValueError("EPUB 缺少 META-INF/container.xml。") from exc
    for element in root.iter():
        if local_name(element.tag) == "rootfile":
            package_path = element.attrib.get("full-path")
            if package_path:
                return package_path
    raise ValueError("无法从 container.xml 找到 OPF package 文件。")


def package_data(archive: zipfile.ZipFile) -> tuple[str, ET.Element, dict[str, dict[str, str]], list[str]]:
    opf_path = container_package_path(archive)
    try:
        package = parse_xml(archive.read(opf_path), opf_path)
    except KeyError as exc:
        raise ValueError(f"EPUB 缺少 OPF 文件：{opf_path}") from exc

    manifest: dict[str, dict[str, str]] = {}
    spine_ids: list[str] = []
    for element in package.iter():
        tag = local_name(element.tag)
        if tag == "item" and element.attrib.get("id") and element.attrib.get("href"):
            manifest[element.attrib["id"]] = {
                "href": element.attrib["href"],
                "media_type": element.attrib.get("media-type", ""),
                "properties": element.attrib.get("properties", ""),
            }
        elif tag == "itemref" and element.attrib.get("idref"):
            spine_ids.append(element.attrib["idref"])
    return posixpath.dirname(opf_path), package, manifest, spine_ids


def nav_entries(nav_root: ET.Element) -> list[TocEntry]:
    entries: list[TocEntry] = []

    def children_of(parent: ET.Element, depth: int) -> None:
        for li in parent:
            if local_name(li.tag) != "li":
                continue
            link: ET.Element | None = None
            nested: ET.Element | None = None
            for child in li:
                if local_name(child.tag) in {"a", "span"} and link is None:
                    link = child
                elif local_name(child.tag) == "ol":
                    nested = child
            if link is not None:
                href = link.attrib.get("href", "")
                title = normalized_text(link)
                if href and title:
                    entries.append(TocEntry(len(entries) + 1, title, href, depth))
            if nested is not None:
                children_of(nested, depth + 1)

    toc_nav: ET.Element | None = None
    for element in nav_root.iter():
        if local_name(element.tag) == "nav":
            epub_type = element.attrib.get(f"{{{EPUB_NS}}}type", "")
            if "toc" in epub_type.split():
                toc_nav = element
                break
    if toc_nav is None:
        toc_nav = next((item for item in nav_root.iter() if local_name(item.tag) == "nav"), None)
    if toc_nav is not None:
        top_list = next((item for item in toc_nav if local_name(item.tag) == "ol"), None)
        if top_list is not None:
            children_of(top_list, 0)
    return entries


def ncx_entries(ncx_root: ET.Element) -> list[TocEntry]:
    entries: list[TocEntry] = []

    def visit(parent: ET.Element, depth: int) -> None:
        for point in parent:
            if local_name(point.tag) != "navpoint":
                continue
            label = ""
            href = ""
            children: ET.Element | None = None
            for child in point:
                tag = local_name(child.tag)
                if tag == "navlabel":
                    label_node = next((item for item in child.iter() if local_name(item.tag) == "text"), None)
                    label = normalized_text(label_node) if label_node is not None else ""
                elif tag == "content":
                    href = child.attrib.get("src", "")
                elif tag == "navpoint":
                    children = point
            if href and label:
                entries.append(TocEntry(len(entries) + 1, label, href, depth))
            for child in point:
                if local_name(child.tag) == "navpoint":
                    visit(point, depth + 1)
                    break

    nav_map = next((item for item in ncx_root.iter() if local_name(item.tag) == "navmap"), None)
    if nav_map is not None:
        visit(nav_map, 0)
    return entries


def title_from_document(archive: zipfile.ZipFile, path: str) -> str:
    try:
        root = parse_xml(archive.read(path), path)
    except (KeyError, ValueError):
        return Path(path).stem
    for element in root.iter():
        if local_name(element.tag) in {"h1", "title"}:
            title = normalized_text(element)
            if title:
                return title
    return Path(path).stem


def get_toc(
    archive: zipfile.ZipFile,
    opf_dir: str,
    package: ET.Element,
    manifest: dict[str, dict[str, str]],
    spine_ids: list[str],
) -> list[TocEntry]:
    nav_item = next(
        (item for item in manifest.values() if "nav" in item["properties"].split()),
        None,
    )
    if nav_item is not None:
        nav_path, _ = member_path(opf_dir, nav_item["href"])
        try:
            entries = nav_entries(parse_xml(archive.read(nav_path), nav_path))
            if entries:
                return entries
        except (KeyError, ValueError):
            pass

    spine = next((element for element in package.iter() if local_name(element.tag) == "spine"), None)
    toc_id = spine.attrib.get("toc", "") if spine is not None else ""
    ncx_item = manifest.get(toc_id) if toc_id else next(
        (item for item in manifest.values() if item["media_type"] == "application/x-dtbncx+xml"), None
    )
    if ncx_item is not None:
        ncx_path, _ = member_path(opf_dir, ncx_item["href"])
        try:
            entries = ncx_entries(parse_xml(archive.read(ncx_path), ncx_path))
            if entries:
                return entries
        except (KeyError, ValueError):
            pass

    fallback: list[TocEntry] = []
    for itemref in spine_ids:
        item = manifest.get(itemref)
        if item is None or not item["media_type"].endswith("xhtml+xml"):
            continue
        path, _ = member_path(opf_dir, item["href"])
        fallback.append(TocEntry(len(fallback) + 1, title_from_document(archive, path), item["href"], 0))
    return fallback


def build_parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def block_texts(body: ET.Element) -> list[tuple[ET.Element, str]]:
    result: list[tuple[ET.Element, str]] = []
    for element in body.iter():
        tag = local_name(element.tag)
        epub_type = element.attrib.get(f"{{{EPUB_NS}}}type", "")
        is_pagebreak = "pagebreak" in epub_type.split()
        if tag in SKIP_TAGS or (tag not in BLOCK_TAGS and not is_pagebreak):
            continue
        value = normalized_text(element)
        if value or is_pagebreak:
            result.append((element, value))
    return result


def select_blocks(
    blocks: list[tuple[ET.Element, str]], target: ET.Element | None, parents: dict[ET.Element, ET.Element]
) -> list[tuple[ET.Element, str]]:
    if target is None:
        return blocks

    target_tag = local_name(target.tag)
    if target_tag in HEADING_LEVELS:
        start = next((index for index, (element, _) in enumerate(blocks) if element is target), None)
        if start is not None:
            level = HEADING_LEVELS[target_tag]
            end = len(blocks)
            for index in range(start + 1, len(blocks)):
                tag = local_name(blocks[index][0].tag)
                if tag in HEADING_LEVELS and HEADING_LEVELS[tag] <= level:
                    end = index
                    break
            return blocks[start:end]

    section_tags = {"article", "section"}
    ancestor = target
    while ancestor in parents:
        ancestor = parents[ancestor]
        if local_name(ancestor.tag) in section_tags:
            return [(element, value) for element, value in blocks if element is ancestor or ancestor in parents_ancestors(element, parents)]
    return [(element, value) for element, value in blocks if element is target or target in parents_ancestors(element, parents)]


def parents_ancestors(element: ET.Element, parents: dict[ET.Element, ET.Element]) -> set[ET.Element]:
    ancestors: set[ET.Element] = set()
    current = element
    while current in parents:
        current = parents[current]
        ancestors.add(current)
    return ancestors


def extract_section(archive: zipfile.ZipFile, opf_dir: str, entry: TocEntry) -> dict[str, object]:
    path, fragment = member_path(opf_dir, entry.href)
    try:
        root = parse_xml(archive.read(path), path)
    except KeyError as exc:
        raise ValueError(f"目录项“{entry.title}”指向不存在的内容文件：{path}") from exc
    body = next((element for element in root.iter() if local_name(element.tag) == "body"), root)
    parents = build_parent_map(root)
    target = None
    if fragment:
        target = next(
            (
                element
                for element in root.iter()
                if element.attrib.get("id") == fragment or element.attrib.get("name") == fragment
            ),
            None,
        )
        if target is None:
            raise ValueError(f"目录项“{entry.title}”引用了不存在的锚点：{path}#{fragment}")
    blocks = select_blocks(block_texts(body), target, parents)
    rendered: list[str] = []
    page_markers: list[str] = []
    for element, value in blocks:
        tag = local_name(element.tag)
        epub_type = element.attrib.get(f"{{{EPUB_NS}}}type", "")
        if "pagebreak" in epub_type.split():
            page = element.attrib.get("title") or element.attrib.get("aria-label") or value
            if page:
                page_markers.append(page)
                rendered.append(f"[页码 {page}]")
        elif tag in HEADING_LEVELS:
            rendered.append(f"{'#' * HEADING_LEVELS[tag]} {value}")
        elif tag == "li":
            rendered.append(f"- {value}")
        elif tag == "pre":
            rendered.append(f"```\n{value}\n```")
        else:
            rendered.append(value)
    content = "\n\n".join(rendered).strip()
    if not content:
        raise ValueError(f"目录项“{entry.title}”没有可提取的正文文本：{path}")
    return {
        "index": entry.index,
        "title": entry.title,
        "depth": entry.depth,
        "source": {"href": path, "fragment": fragment or None, "page_markers": page_markers},
        "content": content,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("epub", type=Path, help="EPUB 文件路径")
    parser.add_argument("--toc", action="store_true", help="仅列出目录，不提取正文")
    parser.add_argument("--chapter", action="append", type=int, help="提取目录编号；可重复传入")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.epub.is_file():
        print(f"EPUB 文件不存在：{args.epub}", file=sys.stderr)
        return 2
    if not zipfile.is_zipfile(args.epub):
        print("输入文件不是有效的 ZIP/EPUB 文件，或文件已损坏。", file=sys.stderr)
        return 2
    if not args.toc and not args.chapter:
        print("请指定 --toc 或至少一个 --chapter 编号。", file=sys.stderr)
        return 2

    try:
        with zipfile.ZipFile(args.epub) as archive:
            opf_dir, package, manifest, spine_ids = package_data(archive)
            toc = get_toc(archive, opf_dir, package, manifest, spine_ids)
            if not toc:
                raise ValueError("EPUB 没有可用目录，也无法从阅读顺序生成章节列表。")
            selected: list[dict[str, object]] = []
            if args.chapter:
                by_index = {entry.index: entry for entry in toc}
                for index in args.chapter:
                    entry = by_index.get(index)
                    if entry is None:
                        raise ValueError(f"目录编号 {index} 不存在；有效编号为 1–{len(toc)}。")
                    selected.append(extract_section(archive, opf_dir, entry))
            output = {
                "epub": str(args.epub.resolve()),
                "toc": [asdict(entry) for entry in toc],
                "chapters": selected,
            }
    except (OSError, zipfile.BadZipFile, RuntimeError, ValueError, ET.ParseError) as exc:
        print(f"EPUB 提取失败：{exc}", file=sys.stderr)
        return 1

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
