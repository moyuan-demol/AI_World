"""Upload safety helpers: filename sanitiser and content signature checks."""

import re
from pathlib import Path

from app.core.errors import ValidationError

_UNSAFE_CHARS = re.compile(r"[^0-9A-Za-z._\-\u4e00-\u9fff]+")
_MAX_FILENAME_LENGTH = 120

_PDF_SIGNATURE = b"%PDF"
_ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")


def sanitize_filename(name: str) -> str:
    """Strip directories, control characters and unsafe symbols.

    Prevents path traversal such as ../../etc/passwd and weird unicode tricks
    before the name is ever used on disk.
    """
    base = Path(name or "").name
    base = base.replace("\x00", "")
    cleaned = _UNSAFE_CHARS.sub("_", base).strip("._ ")
    if not cleaned:
        cleaned = "upload"
    if len(cleaned) > _MAX_FILENAME_LENGTH:
        suffix = Path(cleaned).suffix[:16]
        stem = Path(cleaned).stem[: _MAX_FILENAME_LENGTH - len(suffix)]
        cleaned = stem + suffix
    return cleaned


def validate_signature(filename: str, data: bytes) -> None:
    """Reject a file whose content does not match its extension.

    Blocks the classic trick of renaming an executable or script to .pdf or
    .docx in order to get it stored on the server.
    """
    suffix = Path(filename).suffix.lower()
    if not data:
        raise ValidationError("文件内容为空")
    if suffix == ".pdf" and not data.lstrip()[:4].startswith(_PDF_SIGNATURE):
        raise ValidationError("文件内容不是有效的 PDF（签名校验失败）")
    if suffix == ".xls" and not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise ValidationError("文件内容不是有效的 XLS（签名校验失败）")
    if suffix in {".docx", ".xlsx", ".xlsm", ".pptx", ".odt", ".ods", ".odp"} and not data.startswith(
        _ZIP_SIGNATURES
    ):
        raise ValidationError("文件内容与扩展名不符（签名校验失败）")
    binary_suffixes = {
        ".pdf",
        ".docx",
        ".xlsx",
        ".xlsm",
        ".xls",
        ".pptx",
        ".odt",
        ".ods",
        ".odp",
    }
    if suffix not in binary_suffixes and b"\x00" in data[:2048]:
        raise ValidationError("文本文件包含二进制内容，已拒绝上传")
