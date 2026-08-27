"""Streaming markdown display with word-wrap and terminal-resize reflow."""

from __future__ import annotations

import errno
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from io import StringIO
from typing import Any, Iterator

from rich import box
from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.constrain import Constrain
from rich.markdown import CodeBlock, Heading, Markdown, TableElement
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

CODE_THEME = "monokai"

CHAT_THEME = Theme(
    {
        "markdown.h1": "bold bright_cyan",
        "markdown.h2": "bold cyan",
        "markdown.h3": "bold bright_blue",
        "markdown.h4": "bold blue",
        "markdown.h5": "bold magenta",
        "markdown.h6": "bold bright_magenta",
        "markdown.strong": "bold yellow",
        "markdown.em": "italic bright_green",
        "markdown.emph": "italic bright_green",
        "markdown.s": "strike dim",
        "markdown.code": "bold bright_yellow on grey23",
        "markdown.code_block": "cyan",
        "markdown.block_quote": "italic #a0a0a0",
        "markdown.link": "underline bright_blue",
        "markdown.link_url": "underline blue",
        "markdown.item.bullet": "bold bright_cyan",
        "markdown.item.number": "bold cyan",
        "markdown.hr": "dim",
        "markdown.table.border": "bright_blue",
        "markdown.table.header": "bold bright_cyan",
        "markdown.paragraph": "",
    }
)


def stabilize_markdown(markup: str) -> str:
    """Close an unclosed fenced code block so incomplete streams still render."""
    fence = "```"
    count = 0
    pos = 0
    while True:
        idx = markup.find(fence, pos)
        if idx < 0:
            break
        count += 1
        pos = idx + len(fence)
    if count % 2:
        return markup + "\n```"
    return markup


class ChatHeading(Heading):
    """Left-aligned headings so wrap math stays aligned with the chat column."""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        text = self.text.copy()
        text.overflow = "ellipsis"
        text.no_wrap = True
        yield text


class ChatCodeBlock(CodeBlock):
    """Fenced code in a panel, wrapped so long lines never overflow the terminal."""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        code = str(self.text).rstrip()
        lexer = self.lexer_name or "text"
        syntax = Syntax(
            code,
            lexer,
            theme=self.theme,
            word_wrap=True,
            padding=0,
            background_color="default",
        )
        width = max(8, options.max_width)
        title = None if lexer in {"", "text"} else lexer
        panel = Panel(
            syntax,
            box=box.ROUNDED,
            border_style="markdown.code_block",
            padding=(0, 1),
            expand=True,
            width=width,
            title=title,
            title_align="left",
        )
        yield Constrain(panel, width=width)


class ChatTable(TableElement):
    """Markdown tables constrained to the console width with folding cells."""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        width = max(8, options.max_width)
        table = Table(
            box=box.ROUNDED,
            pad_edge=False,
            collapse_padding=True,
            show_edge=True,
            expand=True,
            style="markdown.table.border",
            width=width,
            padding=(0, 1),
        )
        if self.header is not None and self.header.row is not None:
            for column in self.header.row.cells:
                heading = column.content.copy()
                heading.stylize("markdown.table.header")
                heading.overflow = "fold"
                heading.no_wrap = False
                table.add_column(
                    heading,
                    overflow="fold",
                    no_wrap=False,
                )
        if self.body is not None:
            for row in self.body.rows:
                cells = []
                for element in row.cells:
                    cell = element.content.copy()
                    cell.overflow = "fold"
                    cell.no_wrap = False
                    cells.append(cell)
                table.add_row(*cells)
        yield Constrain(table, width=width)


class ChatMarkdown(Markdown):
    """Markdown tuned for a wrapping chat column."""

    elements = {
        **Markdown.elements,
        "heading_open": ChatHeading,
        "fence": ChatCodeBlock,
        "code_block": ChatCodeBlock,
        "table_open": ChatTable,
    }

    def __init__(self, markup: str, *, code_theme: str = CODE_THEME) -> None:
        super().__init__(
            stabilize_markdown(markup),
            code_theme=code_theme,
            justify=None,
            hyperlinks=True,
        )


@dataclass
class StreamSession:
    console: Console
    speaker: str
    text: str = ""
    tools: list[str] = field(default_factory=list)
    _rows: int = 0
    _last_draw: float = 0.0

    def renderable(self) -> object:
        parts: list[object] = []
        if self.speaker:
            parts.append(Text(self.speaker, style="bold magenta"))
        for tool in self.tools:
            parts.append(Text(f"→ {tool}", style="dim italic"))
        if self.text:
            parts.append(ChatMarkdown(self.text))
        return Group(*parts) if parts else Text("")

    def start(self) -> None:
        self._rows = 0
        self._last_draw = 0.0

    def feed(self, chunk: str) -> None:
        if chunk:
            self.text += chunk
            self.redraw()

    def tool(self, name: str | None, args: Any) -> None:
        label = name or "tool"
        self.tools.append(f"{label} {args}" if args is not None else label)
        self.redraw(force=True)

    def stop(self) -> None:
        self.redraw(force=True)
        self.commit()

    def commit(self) -> None:
        """Leave the current block on screen and stop tracking it."""
        file = self.console.file
        if self._rows > 0:
            file.write("\n\x1b[?25h")
            file.flush()
        elif self.speaker or self.text or self.tools:
            self.console.print(self.renderable())
        self._rows = 0

    def redraw(self, *, force: bool = False) -> None:
        file = self.console.file
        if not hasattr(file, "isatty") or not file.isatty():
            return
        now = time.monotonic()
        if not force and now - self._last_draw < 0.05:
            return
        self._last_draw = now
        output, rows = _render_surface(self.console, self.renderable())
        file.write("\x1b[?25l")
        if self._rows > 0:
            up = self._rows - 1
            if up > 0:
                file.write(f"\r\x1b[{up}A\x1b[J")
            else:
                file.write("\r\x1b[J")
        if output:
            file.write(output)
        file.write("\x1b[?25h")
        file.flush()
        self._rows = rows


def _term_width() -> int:
    cols = shutil.get_terminal_size().columns
    return max(8, cols - 1)


def _render_surface(console: Console, renderable: object) -> tuple[str, int]:
    size = shutil.get_terminal_size()
    buf = StringIO()
    tmp = Console(
        file=buf,
        width=_term_width(),
        height=max(size.lines, 24),
        force_terminal=True,
        force_jupyter=False,
        color_system=console.color_system,
        theme=CHAT_THEME,
        highlight=False,
        soft_wrap=False,
        legacy_windows=False,
    )
    tmp.print(renderable, overflow="fold", crop=True, end="")
    output = buf.getvalue().rstrip("\n")
    if not output:
        return "", 0
    return output, output.count("\n") + 1


class ChatDisplay:
    """Streaming markdown printer. Wraps to the current terminal width; does not reflow on resize."""

    def __init__(self, console: Console) -> None:
        self.console = console
        self._session: StreamSession | None = None

    def attach(self) -> None:
        return

    def invalidate(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            session.commit()

    @contextmanager
    def streaming(self, speaker: str) -> Iterator[StreamSession]:
        self.invalidate()
        session = StreamSession(self.console, speaker)
        self._session = session
        session.start()
        try:
            yield session
        except BaseException:
            session.stop()
            self._session = None
            raise
        session.stop()
        self._session = None

    def read_line(self, prompt: str) -> str:
        while True:
            try:
                return input(prompt)
            except InterruptedError:
                continue
            except OSError as exc:
                if exc.errno != errno.EINTR:
                    raise
                continue
