"""Loader registry: turns a Source into DocumentContent according to its file type (ADR-0001).

A new file type is one module that defines a loader and registers it with `@register`,
plus one import line at the bottom of this file. Nothing downstream changes.
"""

from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, Field


class DocumentContent(BaseModel):
    source_path: str
    text: str | None = None
    images: list[bytes] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


Loader = Callable[[Path], DocumentContent]


class NoLoaderError(Exception):
    pass


_LOADERS: dict[str, Loader] = {}


def register(*extensions: str) -> Callable[[Loader], Loader]:
    def decorate(loader: Loader) -> Loader:
        for ext in extensions:
            _LOADERS[ext.lower()] = loader
        return loader

    return decorate


def file_type(path: Path) -> str | None:
    return path.suffix.lower().lstrip(".") or None


def load(path: Path) -> DocumentContent:
    ext = path.suffix.lower()
    loader = _LOADERS.get(ext)
    if loader is None:
        raise NoLoaderError(f"no loader for {ext or 'files without an extension'}")
    return loader(path)


from sdi.loaders import email_loader, excel_loader, json_loader, pdf_loader  # noqa: E402, F401
