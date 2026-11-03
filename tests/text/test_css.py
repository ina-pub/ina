from __future__ import annotations

from app.text.css import minify


def test_minify_drops_comments_and_whitespace() -> None:
    css = """
    /* a comment */
    .thing  .title ,
    a.title:hover   {
      color : #369 ;  /* inline */
      margin: 0 1px   2px 3px;
      font-family: verdana, arial;
    }
    """
    assert (
        minify(css)
        == '.thing .title,a.title:hover{color:#369;margin:0 1px 2px 3px;font-family:verdana,arial}'
    )


def test_minify_keeps_what_matters() -> None:
    # A descendant pseudo-class keeps its space; strings, urls and !important survive.
    css = """
    .md :not(p) { content: "a  b" ; background: url(/static/r2/x.png) no-repeat !important }
    @media (max-width: 600px) { .side { display : none } }
    @import url("other.css");
    @font-face { font-family: x; src: url(x.woff) }
    """
    assert minify(css) == (
        '.md :not(p){content:"a  b";background:url(/static/r2/x.png) no-repeat!important}'
        '@media (max-width: 600px){.side{display:none}}'
        '@import url("other.css");'
        '@font-face{font-family:x;src:url(x.woff)}'
    )
