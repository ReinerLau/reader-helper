#!/usr/bin/env python3
"""Validate a set of Obsidian knowledge notes and their typed wikilinks."""

from __future__ import annotations

import re
import sys
from pathlib import Path


NOTE_TYPES_PATH = Path(__file__).resolve().parents[1] / "knowledge-types.md"
RELATION_TYPES_PATH = Path(__file__).resolve().parents[1] / "relation-types.md"
TAG_PATTERN = re.compile(r"#[^\s#]+")
RELATION_PATTERN = re.compile(r"- ([^：]+)：\[\[([^\]]+)\]\]")
FOOTNOTE_REF_PATTERN = re.compile(r"\[\^([^\]]+)\]")
FOOTNOTE_DEF_PATTERN = re.compile(r"^\[\^([^\]]+)\]:\s+(.+)$", re.MULTILINE)


def load_types(path: Path) -> set[str]:
    rows = re.findall(r"^\| ([^|]+) \|", path.read_text(encoding="utf-8"), re.MULTILINE)
    return {
        name.strip()
        for name in rows
        if name.strip() != "类型" and set(name.strip()) != {"-"}
    }


def validate_note(
    path: Path,
    text: str,
    note_names: set[str],
    knowledge_types: set[str],
    relation_types: set[str],
) -> list[str]:
    errors: list[str] = []
    prefix = str(path)
    lines = text.splitlines()

    if not lines or not any(line.strip() for line in lines):
        errors.append(f"{prefix}: 笔记不能为空。")

    if any(re.fullmatch(r"# .+", line.strip()) for line in lines):
        errors.append(f"{prefix}: 文件名就是标题，正文不要重复添加一级标题。")

    tag_lines = [line.strip() for line in lines if TAG_PATTERN.fullmatch(line.strip())]
    if len(tag_lines) != 1:
        errors.append(f"{prefix}: 必须且只能包含一行独立的知识类型标签，例如“#概念”。")
    elif tag_lines[0][1:] not in knowledge_types:
        errors.append(f"{prefix}: 使用了未批准的知识类型标签：{tag_lines[0]}")

    if lines and (not lines[0].strip() or not TAG_PATTERN.fullmatch(lines[0].strip())):
        errors.append(f"{prefix}: 正文第一行必须是知识类型标签，例如“#概念”。")

    definitions = FOOTNOTE_DEF_PATTERN.findall(text)
    body = FOOTNOTE_DEF_PATTERN.sub("", text)
    references = FOOTNOTE_REF_PATTERN.findall(body)
    definition_ids = [footnote_id for footnote_id, _ in definitions]
    if not references:
        errors.append(f"{prefix}: 知识正文缺少脚注引用，例如“[^1]”。")
    if not definitions:
        errors.append(f"{prefix}: 缺少脚注出处定义，例如“[^1]: 出处”。")
    if len(definition_ids) != len(set(definition_ids)):
        errors.append(f"{prefix}: 脚注定义编号不能重复。")
    if set(references) - set(definition_ids):
        errors.append(f"{prefix}: 存在没有出处定义的脚注引用。")
    if set(definition_ids) - set(references):
        errors.append(f"{prefix}: 存在未引用的脚注出处定义。")

    in_relations = False
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if re.fullmatch(r"#{1,6} .+", stripped):
            in_relations = stripped == "## 关系"
            continue
        if FOOTNOTE_DEF_PATTERN.fullmatch(stripped):
            in_relations = False
            continue

        has_link_marker = "[[" in line or "]]" in line
        if in_relations and stripped:
            match = RELATION_PATTERN.fullmatch(stripped)
            if not match:
                errors.append(
                    f"{prefix}:{index}: 关系项必须严格写作“- 类型：[[目标笔记文件名]]”。"
                )
                continue
            relation_type, target = match.groups()
            if relation_type not in relation_types:
                errors.append(f"{prefix}:{index}: 未批准的关系类型：{relation_type}")
            if target not in note_names:
                errors.append(f"{prefix}:{index}: 双链目标不在本次生成的笔记中：[[{target}]]")
            if target == path.stem:
                errors.append(f"{prefix}:{index}: 笔记不能链接到自身。")
        elif has_link_marker:
            errors.append(f"{prefix}:{index}: 双链必须作为“## 关系”下的带类型关系项。")

    return errors


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "用法：python3 scripts/validate_notes.py note1.md [note2.md ...]",
            file=sys.stderr,
        )
        return 2

    try:
        knowledge_types = load_types(NOTE_TYPES_PATH)
        relation_types = load_types(RELATION_TYPES_PATH)
    except OSError as exc:
        print(f"无法读取类型规范：{exc}", file=sys.stderr)
        return 1

    paths = [Path(argument) for argument in sys.argv[1:]]
    resolved_paths = [path.resolve() for path in paths]
    if len(set(resolved_paths)) != len(resolved_paths):
        print("错误：笔记路径不能重复。", file=sys.stderr)
        return 1

    note_names: set[str] = set()
    for path in paths:
        if path.suffix.lower() != ".md":
            print(f"错误：只接受 Markdown 笔记：{path}", file=sys.stderr)
            return 1
        if path.stem in note_names:
            print(f"错误：本次笔记文件名重复：{path.stem}", file=sys.stderr)
            return 1
        note_names.add(path.stem)

    errors: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"无法读取笔记 {path}：{exc}")
            continue
        errors.extend(
            validate_note(path, text, note_names, knowledge_types, relation_types)
        )

    if errors:
        for error in errors:
            print(f"错误：{error}", file=sys.stderr)
        return 1

    print(f"有效 Obsidian 笔记：{len(paths)} 篇")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
