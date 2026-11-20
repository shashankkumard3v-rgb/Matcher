"""Which page-measuring tests can run here.

On Linux (and Render) LibreOffice measures pages. On a Windows laptop the only renderer is
usually Microsoft Word, driven over COM: slow, it opens Word ~20 times, and Word sometimes
drops the connection ("Windows fatal exception: code 0x800706ba"). So the Word-based tests
only run when you ask: set MATCHER_TEST_WORD=1 first.
"""
import os

from app.export.render import renderer_name


def can_render() -> bool:
    name = renderer_name()
    if name == "word":
        return os.environ.get("MATCHER_TEST_WORD") == "1"
    return name is not None


WHY = "needs LibreOffice, or Microsoft Word with MATCHER_TEST_WORD=1"
