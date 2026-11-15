See [README.md](README.md) for usage and commands, and [CONTRIBUTING.md](CONTRIBUTING.md) for architecture and design.

## Principles tests

`tests/unit/principles/` holds one test per design commitment. Never
edit, delete, skip or loosen a test there to make a change pass. When
one fails, or the work in hand would need one changed, stop and tell the
owner which commitment it is, what breaks it, and the options. Change a
test there only on the owner's explicit yes. See
[tests/unit/principles/README.md](tests/unit/principles/README.md).
