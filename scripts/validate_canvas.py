#!/usr/bin/env python3
"""Validate the JSON Canvas structure used by reader-helper."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any


NODE_TYPES = {"text"}
SIDES = {"top", "right", "bottom", "left"}
ENDS = {"none", "arrow"}


def load_knowledge_types() -> set[str]:
    taxonomy_path = Path(__file__).resolve().parents[1] / "knowledge-types.md"
    rows = re.findall(r"^\| ([^|]+) \|", taxonomy_path.read_text(encoding="utf-8"), re.MULTILINE)
    return {
        name.strip()
        for name in rows
        if name.strip() != "类型" and set(name.strip()) != {"-"}
    }


def validate(data: Any, knowledge_types: set[str] | None = None) -> list[str]:
    errors: list[str] = []
    if knowledge_types is None:
        try:
            knowledge_types = load_knowledge_types()
        except OSError as exc:
            return [f"无法读取知识类型规范：{exc}"]
    if not isinstance(data, dict):
        return ["根数据必须是 JSON object。"]
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    if not isinstance(nodes, list):
        errors.append("nodes 必须是数组。")
        nodes = []
    if not isinstance(edges, list):
        errors.append("edges 必须是数组。")
        edges = []
    if not nodes:
        errors.append("知识画布至少需要一个节点。")

    ids: set[str] = set()
    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        prefix = f"nodes[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{prefix} 必须是 object。")
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            errors.append(f"{prefix}.id 必须是非空字符串。")
        elif node_id in ids:
            errors.append(f"重复 ID：{node_id}")
        else:
            ids.add(node_id)
            node_ids.add(node_id)
        if node.get("type") not in NODE_TYPES:
            errors.append(f"{prefix}.type 必须是 {', '.join(sorted(NODE_TYPES))} 之一。")
        for key in ("x", "y", "width", "height"):
            value = node.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{prefix}.{key} 必须是数字。")
            elif key in {"width", "height"} and value <= 0:
                errors.append(f"{prefix}.{key} 必须大于 0。")
        if node.get("type") != "text":
            errors.append(f"{prefix}.type 必须是 text，知识画布只允许知识卡片节点。")
        else:
            text = node.get("text")
            if not isinstance(text, str):
                errors.append(f"{prefix}.text 必须是字符串。")
            else:
                lines = text.splitlines()
                # Skip the first line, which is the Markdown title. Knowledge tags
                # are standalone hashtags on subsequent lines, such as #概念.
                tag_lines = [
                    line.strip()
                    for line in lines[1:]
                    if re.fullmatch(r"#[^\s#]+", line.strip())
                ]
                legacy_type_lines = re.findall(
                    r"^\*\*类型\*\*\s+.+?\s*$", text, re.MULTILINE
                )
                if legacy_type_lines:
                    errors.append(f"{prefix}.text 使用旧的“**类型** …”格式；请改用独立的 #类型 标签行。")
                if len(tag_lines) != 1:
                    errors.append(f"{prefix}.text 必须且只能包含一行独立的知识类型标签，例如“#概念”。")
                elif tag_lines[0][1:] not in knowledge_types:
                    errors.append(f"{prefix} 使用了未批准的知识类型标签：{tag_lines[0]}")
                if not re.search(r"^\*\*出处\*\*\s+.+$", text, re.MULTILINE):
                    errors.append(f"{prefix}.text 缺少出处。")

    for index, edge in enumerate(edges):
        prefix = f"edges[{index}]"
        if not isinstance(edge, dict):
            errors.append(f"{prefix} 必须是 object。")
            continue
        edge_id = edge.get("id")
        if not isinstance(edge_id, str) or not edge_id:
            errors.append(f"{prefix}.id 必须是非空字符串。")
        elif edge_id in ids:
            errors.append(f"重复 ID：{edge_id}")
        else:
            ids.add(edge_id)
        for key in ("fromNode", "toNode"):
            endpoint = edge.get(key)
            if not isinstance(endpoint, str) or endpoint not in node_ids:
                errors.append(f"{prefix}.{key} 必须引用已有节点。")
        for key in ("fromSide", "toSide"):
            if key in edge and edge[key] not in SIDES:
                errors.append(f"{prefix}.{key} 必须是 {', '.join(sorted(SIDES))} 之一。")
        for key in ("fromEnd", "toEnd"):
            if key in edge and edge[key] not in ENDS:
                errors.append(f"{prefix}.{key} 必须是 {', '.join(sorted(ENDS))} 之一。")
        if not isinstance(edge.get("label"), str) or not edge["label"].strip():
            errors.append(f"{prefix}.label 必须是非空字符串。")
    return errors


def main() -> int:
    if len(sys.argv) != 2:
        print("用法：python3 scripts/validate_canvas.py path/to/file.canvas", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Canvas JSON 读取失败：{exc}", file=sys.stderr)
        return 1
    errors = validate(data)
    if errors:
        for error in errors:
            print(f"错误：{error}", file=sys.stderr)
        return 1
    print(f"有效 JSON Canvas：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
