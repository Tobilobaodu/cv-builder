"""Turn a fetched HTML page into the readable text underneath it.

`ssrf_safe_fetch` returns the response body verbatim, so a job post fetched
from a URL arrives as markup. That markup used to be stored as
`job_posts.raw_text`, shown to the user, and sent to the model — which
wastes tokens on tag soup and buries the actual posting inside navigation,
cookie banners and inline scripts.

Deliberately stdlib-only (`html.parser`). The backend pins its dependency
set and audits it, so a new third-party parser is a bigger commitment than
the code below, and nothing here needs a real DOM: we are extracting text,
not rendering a page.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# Content inside these never belongs in the text. script/style matter most:
# their bodies are text nodes, so a naive tag-stripper emits the whole of a
# page's JavaScript as if it were prose. The chrome tags after them are the
# second half of the problem — a careers page is mostly menus, consent
# dialogs, login forms and footers by volume.
_DROP_CONTENT_TAGS = frozenset(
    {
        "script", "style", "noscript", "template", "svg", "head", "iframe",
        "canvas", "nav", "aside", "footer", "form", "dialog", "select",
        "button", "figure", "video", "audio",
    }
)

# Elements whose id/class marks them as page furniture. Matched against the
# raw attribute values, so it catches cookie-banner, js-consent-modal and
# CookieConsent__root alike. Deliberately narrow: every term here appeared
# as noise in a real fetched posting, and a false positive silently deletes
# real content.
_BOILERPLATE_ATTR = re.compile(
    r"cookie|consent|gdpr|newsletter|subscribe|breadcrumb|skip-?link"
    r"|social-?(share|links)|site-?(header|footer)|nav(bar|igation)?-"
    r"|menu-|modal|banner|announce",
    re.IGNORECASE,
)

# Void elements never open a scope, so they must not be pushed onto the
# element stack — otherwise the stack drifts and suppression ends early.
_VOID_TAGS = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }
)

# Tags that end the current line. Without these, a page collapses into one
# unreadable paragraph and the section structure a job post relies on
# (Responsibilities / Requirements / Benefits) is lost.
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "blockquote", "div", "dd", "dl", "dt",
        "fieldset", "figcaption", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
        "section", "table", "tbody", "td", "th", "thead", "tr", "ul", "br",
    }
)

_MAIN_TAGS = frozenset({"main", "article"})

_WS_RUN = re.compile(r"[^\S\n]+")
_BLANK_RUN = re.compile(r"\n{3,}")
_ORPHAN_BULLET = re.compile(r"(?m)^- *\n+")


class _TextExtractor(HTMLParser):
    """Collect text, skipping whole subtrees that are page furniture.

    Suppression is tracked by element depth rather than by tag name: a
    div marked as a cookie banner containing further divs has to stay
    suppressed until *its* closing tag, not the first one encountered.
    """

    def __init__(self, main_only: bool = False) -> None:
        # convert_charrefs=True means handle_data receives text with
        # entities already decoded (&amp; -> &, &nbsp; -> U+00A0).
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._open: list[str] = []
        self._suppress_at: int | None = None
        self._main_only = main_only
        self._main_at: int | None = None
        self._saw_main = False

    # ── suppression bookkeeping ──────────────────────────────────────
    @property
    def _suppressed(self) -> bool:
        if self._suppress_at is not None:
            return True
        # Outside main/article when we have been told to keep only that.
        return self._main_only and self._main_at is None

    def _is_boilerplate(self, attrs) -> bool:
        for name, value in attrs:
            if name in ("id", "class", "data-testid", "aria-label") and value:
                if _BOILERPLATE_ATTR.search(value):
                    return True
        return False

    # ── HTMLParser hooks ─────────────────────────────────────────────
    def handle_starttag(self, tag: str, attrs) -> None:
        void = tag in _VOID_TAGS
        if not void:
            self._open.append(tag)
            depth = len(self._open)
        else:
            depth = len(self._open) + 1

        if self._main_only and tag in _MAIN_TAGS and self._main_at is None:
            self._main_at = depth
            self._saw_main = True
            return

        if self._suppress_at is None and (
            tag in _DROP_CONTENT_TAGS or self._is_boilerplate(attrs)
        ):
            self._suppress_at = depth
            return

        if self._suppressed:
            return

        if tag == "li":
            # Keep list structure visible — job posts carry their
            # requirements as bullets, and the CV side preserves them too.
            self._parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return

        # Unwind to the matching open tag so stray or mismatched end tags
        # cannot desynchronise the stack.
        if tag not in self._open:
            return
        while self._open and self._open[-1] != tag:
            self._open.pop()
        depth = len(self._open)
        self._open.pop()

        if self._suppress_at is not None and depth <= self._suppress_at:
            self._suppress_at = None
            return
        if self._main_at is not None and depth <= self._main_at:
            self._main_at = None
            return
        if self._suppressed:
            return

        if tag == "li":
            # No trailing newline: the next li opens with one, so bullets
            # stay on adjacent lines instead of double-spaced.
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)

    @property
    def found_main(self) -> bool:
        return self._saw_main


def looks_like_html(raw: str) -> bool:
    """True when the body is markup rather than plain text.

    Checked before stripping so a text/plain posting that happens to
    contain a < or a > is left exactly as it was fetched.
    """
    return re.search(
        r"<\s*(!doctype\s+html|html|head|body|div|p|span|br|table|ul|script)\b",
        raw[:4096],
        re.IGNORECASE,
    ) is not None


def tidy_text(text: str) -> str:
    """Collapse whitespace and blank-line runs without touching content."""
    text = text.replace("\xa0", " ")
    text = _WS_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    # A list item whose content is wrapped in a block element (<li><p>…) puts
    # the bullet on a line of its own, which reads as a stray dash and hides
    # the list structure the marker exists to show. Pull the text back up.
    text = _ORPHAN_BULLET.sub("- ", text)
    return _BLANK_RUN.sub("\n\n", text).strip()


def html_to_text(raw: str, main_only: bool = False) -> str:
    """Extract readable text from an HTML document.

    Args:
        raw: The document, or a fragment.
        main_only: Keep only the contents of the first main/article element.
            Falls back to the whole document when the page has neither.

    Returns `raw` unchanged when it does not look like HTML, or when
    stripping would leave nothing (a page that is entirely script, for
    instance) — losing the body outright would be worse than keeping it.
    """
    if not raw or not looks_like_html(raw):
        return raw

    parser = _TextExtractor(main_only=main_only)
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        # HTMLParser is lenient, but never let malformed markup take out
        # the fetch: the raw body is still more useful than a failed job.
        return raw

    if main_only and not parser.found_main:
        return html_to_text(raw, main_only=False)

    text = tidy_text(parser.text())
    if text:
        return text
    if main_only:
        return html_to_text(raw, main_only=False)
    return raw
