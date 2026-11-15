# -*- coding: utf-8 -*-
"""Модуль информационной безопасности «Конвертер АМИРС».

Обеспечивает защиту при обработке недоверенных входных файлов и персональных данных
(152-ФЗ) в контуре судебных участков Республики Коми:
- защита XML-парсера от XXE и XML-бомб (Billion Laughs);
- защита экспортируемых CSV/Excel-отчётов от Formula Injection;
- санитизация диагностических логов от утечки ПДн (СНИЛС, паспорт, счёт);
- проверка элементов ZIP-архивов от ZipSlip (path traversal) и Zip-бомб.
"""
from __future__ import annotations

import os
import posixpath
import re
import xml.etree.ElementTree as ET
from typing import Union

_XML_FORBIDDEN_DECL = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

# Паттерны ПДн для маскирования в диагностических логах (amirs_log.txt)
_RE_SNILS = re.compile(r"\b\d{3}[-\s]?\d{3}[-\s]?\d{3}[-\s]?\d{2}\b")
_RE_PASSPORT = re.compile(r"\b\d{2}\s?\d{2}\s+\d{6}\b")
_RE_ACCOUNT = re.compile(r"\b\d{20}\b")
_RE_INN_12 = re.compile(r"(?i)(инн\s*[:№]?\s*)\d{10,12}\b")

_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_NUMERIC_RE = re.compile(r"^-\d+(?:[.,]\d+)?$")


def safe_xml_fromstring(
    data: Union[bytes, str],
    max_bytes: int = 20 * 1024 * 1024,
) -> ET.Element:
    """Безопасный разбор XML: отклоняет DOCTYPE/ENTITY (XXE, XML-бомбы) и слишком большие файлы."""
    if isinstance(data, bytes):
        if len(data) > max_bytes:
            raise ValueError(
                f"XML-документ превышает допустимый размер ({len(data)} > {max_bytes} байт)"
            )
        head_text = data[:8192].decode("utf-8", errors="ignore")
        if _XML_FORBIDDEN_DECL.search(head_text):
            raise ValueError("Запрещённая декларация DOCTYPE/ENTITY в XML")
        return ET.fromstring(data)
    text = str(data)
    raw_len = len(text.encode("utf-8", errors="ignore"))
    if raw_len > max_bytes:
        raise ValueError(
            f"XML-документ превышает допустимый размер ({raw_len} > {max_bytes} байт)"
        )
    if _XML_FORBIDDEN_DECL.search(text[:8192]):
        raise ValueError("Запрещённая декларация DOCTYPE/ENTITY в XML")
    return ET.fromstring(text)


def sanitize_csv_cell(value: object, allow_negative_numbers: bool = True) -> str:
    """Экранирует потенциально опасные префиксы формул (=, +, -, @, TAB, CR) для CSV/Excel."""
    s = "" if value is None else str(value)
    if not s:
        return s
    stripped = s.lstrip(" ")
    if not stripped:
        return s
    if allow_negative_numbers and _NUMERIC_RE.match(stripped):
        return s
    if s[0] in _CSV_FORMULA_PREFIXES or stripped[0] in _CSV_FORMULA_PREFIXES:
        return "'" + s
    return s


def redact_pii(text: object) -> str:
    """Маскирует СНИЛС, паспортные данные, ИНН и 20-значные счета в строках журнала."""
    s = "" if text is None else str(text)
    if not s:
        return s
    s = _RE_PASSPORT.sub("[ПАСПОРТ]", s)
    s = _RE_ACCOUNT.sub("[СЧЁТ]", s)
    s = _RE_INN_12.sub(r"\1[ИНН]", s)
    s = _RE_SNILS.sub("[СНИЛС]", s)
    return s


def is_safe_archive_member(
    name: str,
    file_size: int,
    compress_size: int = 0,
    *,
    max_file_bytes: int = 10 * 1024 * 1024,
    max_ratio: int = 100,
) -> bool:
    """Проверяет элемент архива на ZipSlip (выход за пределы каталога) и Zip-бомбу."""
    if not name or "\x00" in name:
        return False
    norm = name.replace("\\", "/")
    if norm.startswith("/") or (len(norm) >= 2 and norm[1] == ":"):
        return False
    cleaned = posixpath.normpath(norm)
    if cleaned == ".." or cleaned.startswith("../") or "/../" in f"/{cleaned}/":
        return False
    if file_size < 0 or file_size > max_file_bytes:
        return False
    if compress_size > 0 and file_size > compress_size * max_ratio:
        return False
    return True


def restrict_dir_permissions(path: str) -> None:
    """Ограничивает права доступа к каталогу с ПДн (0700 на POSIX; на Windows наследует профиль)."""
    if os.name == "posix" and os.path.isdir(path):
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
