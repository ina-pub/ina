# Types for the vendored snudown C extension (vendor/snudown/snudown.c).

__version__: str
RENDERER_USERTEXT: int
RENDERER_WIKI: int

def markdown(
    text: str,
    nofollow: bool = ...,
    target: str | None = ...,
    toc_id_prefix: str | None = ...,
    renderer: int = ...,
    enable_toc: bool = ...,
) -> str: ...
