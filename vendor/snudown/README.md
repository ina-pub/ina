# snudown (vendored)

reddit's markdown renderer, a fork of sundown with Python 3 bindings. This is a copy of
[reddit/snudown](https://github.com/reddit/snudown) at commit
`f4c7a9c2d2aabbc5ffdb7687223d13961541274b` (version 1.7.0, 2025-05-23), MIT licensed (`LICENSE`).

PyPI's `snudown` is an empty placeholder, so the app builds this copy as a C extension through
`[tool.uv.sources]` in the root `pyproject.toml`. Building needs only a C compiler.

Changes from upstream:

- `setup.py` is replaced: upstream's imports `distutils` (gone since Python 3.12) and runs `gperf`
  at build time.
- `src/html_entities.h` is generated and committed:
  `gperf src/html_entities.gperf --output-file=src/html_entities.h`.
- `pyproject.toml` is added.
- `src/markdown.c`, where it seeds its hash key (each change is marked `ina:`):
  - on macOS, which has no `getrandom` syscall, it uses `getentropy()`;
  - upstream tested the misspelled `__GLIBC_MINOR`, so glibc's `getrandom()` was never used; the
    check is fixed;
  - the raw-syscall fallback for older libcs now includes `<unistd.h>`, without which newer
    compilers refuse the implicit `syscall()` declaration.
- `src/autolink.c` links ina's communities (its comments mark the changes `ina:`):
  - `in/name` and `/in/name` autolink in place of `r/name`, which is now text; a hyphen just
    before `in/` is not a word boundary, so "check-in/out" stays text;
  - names are 3 to 21 characters, as ina's, joined with `+`; upstream's special cases for
    `/r/reddit.com`, `/r/t:` and `/r/all-` are gone;
  - the prefix check takes a string instead of one character.
- `test_snudown.py`: the `/r/` cases test `/in/`, and the special cases are replaced with ina's.

Apart from those patches, the C sources and `test_snudown.py` are upstream's. To update, copy
`snudown.c`, `src/`, `html/`, `LICENSE` and `test_snudown.py` from a new upstream commit, reapply
the changes above, regenerate the header, and record the commit above.
