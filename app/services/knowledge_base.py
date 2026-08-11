from __future__ import annotations

import logging
import re
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable

from defusedxml import ElementTree

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".txt", ".md", ".html", ".htm", ".docx", ".xlsx", ".pdf"}
DEPARTMENT_TAG_GROUPS = (
    {"产品与技术", "投研条线"},
    {"销售条线", "业务运营", "渠道条线"},
    {"技术条线", "研发条线", "数据条线"},
    {"运营条线"},
    {"职能条线", "人力资源", "财务", "法务", "合规"},
)
INTERNAL_BENEFIT_TOPIC_KEYWORDS = (
    "差旅",
    "出差",
    "住宿",
    "补贴",
    "商业保险",
    "保险",
    "体检",
    "培训",
    "专家访谈",
    "数据库",
    "客户拜访",
    "路演",
)


class _HTMLTextExtractor(HTMLParser):
    """Collect visible HTML text without treating regexes as an HTML parser."""

    _SKIPPED_TAGS = {"script", "style"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._SKIPPED_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


@dataclass(frozen=True)
class KnowledgeChunk:
    """A searchable text chunk from a local knowledge source."""

    source_id: str
    title: str
    path: str
    text: str
    tags: dict[str, tuple[str, ...]] = field(default_factory=dict)


def load_knowledge_chunks(root_dir: str) -> tuple[KnowledgeChunk, ...]:
    """Load and chunk all supported files from the knowledge base directory."""

    root = _resolve_knowledge_root(root_dir)
    if not root.exists():
        logger.warning("knowledge_base_dir_missing")
        return ()
    return _cached_load_knowledge_chunks(str(root), _knowledge_fingerprint(root))


@lru_cache(maxsize=8)
def _cached_load_knowledge_chunks(root_dir: str, _fingerprint: tuple[tuple[str, int, int], ...]) -> tuple[KnowledgeChunk, ...]:
    root = Path(root_dir)

    chunks: list[KnowledgeChunk] = []
    source_index = 1
    for path in sorted(_iter_files(root)):
        text = _extract_text(path)
        if not text:
            continue
        tags = _extract_frontmatter_tags(text)
        text = _strip_frontmatter(text)
        title = path.stem
        for chunk_text, chunk_tags in _chunk_text_with_tags(text, tags):
            chunks.append(KnowledgeChunk(f"S{source_index}", title, str(path), chunk_text, chunk_tags))
        source_index += 1
    return tuple(chunks)


def _clear_knowledge_cache() -> None:
    _cached_load_knowledge_chunks.cache_clear()


load_knowledge_chunks.cache_clear = _clear_knowledge_cache  # type: ignore[attr-defined]


def retrieve_relevant_chunks(
    query: str,
    root_dir: str,
    limit: int = 6,
    *,
    user_tags: dict[str, str | list[str] | tuple[str, ...]] | None = None,
) -> list[KnowledgeChunk]:
    """Return top matching knowledge chunks for a question."""

    active_tags = user_tags or {}
    chunks = [chunk for chunk in load_knowledge_chunks(root_dir) if _is_chunk_allowed(chunk, active_tags, query=query)]
    scored = [(_score_chunk(query, chunk, active_tags), chunk) for chunk in chunks]
    return [chunk for score, chunk in sorted(scored, key=lambda item: item[0], reverse=True) if score > 0][:limit]


def _resolve_knowledge_root(root_dir: str) -> Path:
    configured = Path(root_dir).expanduser()
    candidates = [
        configured,
        Path.cwd() / "examples" / "knowledge_base",
        Path(__file__).resolve().parents[2] / "examples" / "knowledge_base",
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists():
            return resolved
    return configured.resolve()


def _iter_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if path.name.startswith(".") or not path.is_file():
            continue
        if path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path


def _knowledge_fingerprint(root: Path) -> tuple[tuple[str, int, int], ...]:
    return tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in sorted(_iter_files(root)))


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix in {".html", ".htm"}:
            return _html_to_text(path.read_text(encoding="utf-8", errors="ignore"))
        if suffix == ".docx":
            return _docx_to_text(path)
        if suffix == ".xlsx":
            return _xlsx_to_text(path)
        if suffix == ".pdf":
            return _pdf_to_text(path)
    except Exception as exc:
        logger.warning("knowledge_source_extract_failed source_type=%s error_type=%s", suffix, type(exc).__name__)
    return ""


def _html_to_text(raw_html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(raw_html)
    parser.close()
    return _normalize_text(" ".join(parser.parts))


def _docx_to_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_files = [name for name in archive.namelist() if name.startswith("word/") and name.endswith(".xml")]
        parts: list[str] = []
        for name in xml_files:
            root = ElementTree.fromstring(archive.read(name))
            for node in root.iter():
                if node.tag.endswith("}t") and node.text:
                    parts.append(node.text)
        return _normalize_text("\n".join(parts))


def _xlsx_to_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _xlsx_shared_strings(archive)
        sheet_names = [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")]
        values: list[str] = []
        for sheet_name in sheet_names:
            root = ElementTree.fromstring(archive.read(sheet_name))
            for cell in root.iter():
                if not cell.tag.endswith("}c"):
                    continue
                cell_type = cell.attrib.get("t")
                value = next((child.text for child in cell if child.tag.endswith("}v")), None)
                inline = "".join(child.text or "" for child in cell.iter() if child.tag.endswith("}t"))
                if cell_type == "s" and value and value.isdigit() and int(value) < len(shared_strings):
                    values.append(shared_strings[int(value)])
                elif inline:
                    values.append(inline)
                elif value:
                    values.append(value)
        return _normalize_text("\n".join(values))


def _xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root:
        text = "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
        strings.append(text)
    return strings


def _pdf_to_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.warning("pypdf_not_installed")
        return ""

    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return _normalize_text(text)


def _chunk_text(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    sections = re.split(r"(?m)(?=^#{1,4}\s+)", text)
    chunks: list[str] = []
    for section in sections:
        chunks.extend(_chunk_normalized_text(section, size=size, overlap=overlap))
    return chunks


def _chunk_text_with_tags(
    text: str,
    base_tags: dict[str, tuple[str, ...]],
    *,
    size: int = 900,
    overlap: int = 120,
) -> list[tuple[str, dict[str, tuple[str, ...]]]]:
    sections = re.split(r"(?m)(?=^#{1,4}\s+)", text)
    chunks: list[tuple[str, dict[str, tuple[str, ...]]]] = []
    for section in sections:
        section_tags, cleaned = _extract_inline_tags(section)
        merged_tags = dict(base_tags)
        merged_tags.update(section_tags)
        for chunk in _chunk_normalized_text(cleaned, size=size, overlap=overlap):
            chunks.append((chunk, merged_tags))
    return chunks


def _chunk_normalized_text(text: str, size: int, overlap: int) -> list[str]:
    normalized = _normalize_text(text)
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        chunks.append(normalized[start : start + size])
        start += max(size - overlap, 1)
    return chunks


def _extract_frontmatter_tags(text: str) -> dict[str, tuple[str, ...]]:
    match = re.match(r"(?s)^---\s*\n(.*?)\n---\s*\n", text)
    if not match:
        return {}
    tags: dict[str, tuple[str, ...]] = {}
    for raw_line in match.group(1).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        values = tuple(_split_tag_values(raw_value))
        if values:
            tags[key.strip()] = values
    return tags


def _strip_frontmatter(text: str) -> str:
    return re.sub(r"(?s)^---\s*\n.*?\n---\s*\n", "", text, count=1)


def _split_tag_values(raw_value: str) -> list[str]:
    cleaned = raw_value.strip().strip("[]")
    values = re.split(r"[,，、/|]", cleaned)
    return [value.strip().strip("\"'") for value in values if value.strip().strip("\"'")]


def _extract_inline_tags(section: str) -> tuple[dict[str, tuple[str, ...]], str]:
    tag_pattern = r"(?s)<!--\s*tags:\s*(.*?)\s*-->"
    match = re.search(tag_pattern, section)
    if not match:
        return {}, section
    tags: dict[str, tuple[str, ...]] = {}
    for item in re.split(r"[;；\n]", match.group(1)):
        if not item.strip():
            continue
        if "=" in item:
            key, raw_value = item.split("=", 1)
        elif ":" in item:
            key, raw_value = item.split(":", 1)
        else:
            continue
        values = tuple(_split_tag_values(raw_value))
        if values:
            tags[key.strip()] = values
    return tags, re.sub(tag_pattern, "", section, count=1).strip()


def _is_chunk_allowed(
    chunk: KnowledgeChunk,
    user_tags: dict[str, str | list[str] | tuple[str, ...]],
    *,
    query: str = "",
) -> bool:
    if not chunk.tags:
        return True
    requested_departments = _requested_department_values(query)
    if requested_departments and _is_internal_benefit_chunk(chunk):
        user_departments = _expanded_department_values(_user_tag_values(user_tags, "department"))
        if user_departments and not user_departments & requested_departments:
            return False
    audience = set(chunk.tags.get("audience", ()))
    if audience & {"formal_employee", "regular_employee", "正式员工", "正职员工"}:
        user_status = _user_tag_values(user_tags, "employee_status") | _user_tag_values(user_tags, "audience")
        if not user_status & {"formal_employee", "regular_employee", "正式员工", "正职员工", "regular"}:
            return False
    for key in ("employment_type", "city", "department", "position", "job_level", "political_status", "fund_qualification_status"):
        allowed_values = set(chunk.tags.get(key, ()))
        if not allowed_values:
            continue
        if allowed_values & {"通用", "全部", "不限", "全国", "all"}:
            continue
        user_values = _user_tag_values(user_tags, key)
        if not user_values or not user_values & allowed_values:
            return False
    return True


def _user_tag_values(user_tags: dict[str, str | list[str] | tuple[str, ...]], key: str) -> set[str]:
    value = user_tags.get(key)
    if value is None:
        return set()
    if isinstance(value, str):
        return {item for item in _split_tag_values(value) if item}
    return {str(item).strip() for item in value if str(item).strip()}


def _is_internal_benefit_chunk(chunk: KnowledgeChunk) -> bool:
    return bool(set(chunk.tags.get("sensitivity", ())) & {"internal_benefits"})


def _requested_department_values(query: str) -> set[str]:
    matched = {value for group in DEPARTMENT_TAG_GROUPS for value in group if value in query}
    return _expanded_department_values(matched)


def _expanded_department_values(values: set[str]) -> set[str]:
    expanded = set(values)
    for group in DEPARTMENT_TAG_GROUPS:
        if expanded & group:
            expanded |= group
    return expanded


def _score_chunk(
    query: str,
    chunk: KnowledgeChunk,
    user_tags: dict[str, str | list[str] | tuple[str, ...]] | None = None,
) -> float:
    query_terms = _expanded_terms(query)
    if not query_terms:
        return 0
    text = chunk.text.lower()
    title = chunk.title.lower()
    score = 0.0
    for term in query_terms:
        if term in text:
            score += 3.0
        if term in title:
            score += 2.0
    requested_cities = [city for city in ("上海", "北京", "广州", "深圳", "杭州") if city in query]
    for city in requested_cities:
        chunk_title_cities = [known_city for known_city in ("上海", "北京", "广州", "深圳", "杭州", "广东", "浙江") if known_city in chunk.title]
        if city in chunk.title:
            score += 28.0
        elif city in chunk.text:
            score += 8.0
        else:
            score -= 10.0
        if chunk_title_cities and city not in chunk_title_cities:
            score -= 24.0
    searchable = f"{chunk.title} {chunk.text}"
    if _is_salary_standard_query(query):
        if any(keyword in searchable for keyword in ("最低工资", "工资标准")):
            score += 18.0
        if any(keyword in searchable for keyword in ("缴费工资基数", "社保缴费基数", "缴费基数", "缴存基数", "住房公积金")):
            score += 6.0
    if any(keyword in query for keyword in ("试用期", "转正")) and "转正" in searchable:
        score += 18.0
    if score > 0 and _is_formal_employee_context(query, chunk, user_tags or {}):
        score += 24.0
    if score > 0 and _is_internal_benefit_chunk(chunk):
        for keyword in INTERNAL_BENEFIT_TOPIC_KEYWORDS:
            if keyword in query and keyword in title:
                score += 30.0
            elif keyword in query and keyword in chunk.text:
                score += 8.0
        for key in ("job_level", "department", "city", "position"):
            allowed_values = set(chunk.tags.get(key, ()))
            if allowed_values and not allowed_values & {"通用", "全部", "不限", "全国", "all"}:
                user_values = _user_tag_values(user_tags or {}, key)
                if user_values and user_values & allowed_values:
                    score += 24.0
    return score


def _expanded_terms(text: str) -> set[str]:
    terms = _terms(text)
    if _is_salary_standard_query(text):
        terms.update(_terms("最低工资 工资标准 社保缴费基数 缴费工资基数 公积金缴存基数 住房公积金"))
    return terms


def _is_salary_standard_query(text: str) -> bool:
    return any(keyword in text for keyword in ("薪酬标准", "薪资标准", "工资标准", "薪酬", "薪资"))


def _is_formal_employee_context(
    query: str,
    chunk: KnowledgeChunk,
    user_tags: dict[str, str | list[str] | tuple[str, ...]],
) -> bool:
    formal_values = {"formal_employee", "regular_employee", "正式员工", "正职员工", "regular"}
    chunk_is_formal = bool(set(chunk.tags.get("audience", ())) & formal_values)
    if not chunk_is_formal:
        return False
    if any(value in query for value in ("正式员工", "正职员工", "已转正", "转正后")):
        return True
    return bool(_user_tag_values(user_tags, "employee_status") & formal_values)


def _terms(text: str) -> set[str]:
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9]+", lowered))
    chinese = re.findall(r"[\u4e00-\u9fff]", lowered)
    words.update("".join(chinese[index : index + 2]) for index in range(max(len(chinese) - 1, 0)))
    words.update("".join(chinese[index : index + 3]) for index in range(max(len(chinese) - 2, 0)))
    return {word for word in words if word.strip()}


def _normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()
