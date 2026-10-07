from functools import cache
from pathlib import Path

_TEMPLATE_PATH = Path(__file__).with_name("live_status.html")


@cache
def _template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def render_live_status_html(mcp_urls: dict[str, str]) -> str:
    links = " ".join(
        f'<a href="{url}" target="_blank" rel="noreferrer">{name}</a>' for name, url in mcp_urls.items()
    )
    return _template().replace("{{MCP_LINKS}}", links)
