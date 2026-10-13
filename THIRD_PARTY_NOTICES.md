# Third-party notices

Kern is proprietary software (see [LICENSE](LICENSE)). It incorporates or
depends on components written by other people and distributed under their own
licences. **Those licences are not affected by Kern's licence, and nothing in
Kern's LICENSE restricts the rights they grant you.**

This file exists so that the proprietary claim is accurate: a blanket
"all rights reserved" over a tree that vendors MIT code would be wrong, and it
is exactly the kind of wrong that gets a claim thrown out.

---

## 1. Source vendored in this repository

These files are committed here and redistributed with it. Their licences travel
with them.

### marked — `webapp/static/operator/vendor/marked.min.js`

- **Version:** v12.0.2 · **Licence:** MIT · **Copyright:** (c) 2011-2024, Christopher Jeffrey
- **Purpose:** renders the Operator pages' markdown in the browser; no build step, so the file is vendored as-is.

```
MIT License

Copyright (c) 2011-2024, Christopher Jeffrey

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

> `webapp/static/prototype/support.js` is **not** third-party: it is generated
> from this project's own `dc-runtime/src/*.ts`.

---

## 2. Python dependencies (installed, not vendored)

These are **not** committed to this repository and are **not** redistributed by
it: `pip`/`uv` installs them from their own indexes. They are listed because any
*container image* or *bundle* that ships them **does** redistribute them, and
then their notices must ship too.

Declared in `pyproject.toml`:

| Package | Floor | Licence (verify per release) |
|---|---|---|
| numpy | >=1.24 | BSD-3-Clause |
| pandas | >=2.0 | BSD-3-Clause |
| scipy | >=1.10 | BSD-3-Clause |
| matplotlib | >=3.7 | PSF-based (BSD-compatible) |
| openpyxl | >=3.1 | MIT |
| PyYAML | >=6.0 | MIT |

Optional extras add, among others: `fastapi`/`uvicorn` (MIT/BSD), `anthropic`
(MIT), `mcp` (MIT), `bs4` (MIT), `rapidfuzz` (MIT), `price-parser` (MIT),
`python-stdnum` (LGPL-2.1-or-later — **copyleft: check before static
distribution**), `extruct` (MIT).

**The table above is a starting point, not the authoritative record.** Before
publishing an image or shipping a bundle, generate the real list from the
environment that actually ships, and include it in the artifact:

```bash
pip install pip-licenses
pip-licenses --format=markdown --with-urls --with-license-file > THIRD_PARTY_LICENSES.txt
```

`python-stdnum` is the one to look at carefully: it is LGPL, and LGPL obligations
differ between "imported at runtime" and "bundled into an image".

---

## 3. The Odoo addon

`odoo_addon/<version>/kern_dry_run/` is **first-party** code, distributed under
the **Odoo Proprietary License v1.0 (OPL-1)** — a separate licence that governs
that module on the Odoo Apps Store. See its own `LICENSE` and `COPYRIGHT` files.
It is listed here only so the two licences are not confused: the root LICENSE
does not apply to the addon, and OPL-1 does not apply to the engine.

---

## 4. Curated sources in the knowledge graph

The knowledge graph under `knowledge/scm-books/` is **derived from 24
copyrighted books and papers** owned by their authors and publishers. What is
committed are concept labels, provenance and citations — not the source texts.

Two consequences, and both are policy, not preference:

1. **The raw graph never leaves.** It stays internal, is never distributed to a
   client, and is never bundled into a deliverable. Only the *citations attached
   to a result* are surfaced — that is what `scm_agent/knowledge.py`'s internal
   source filter is for.
2. **If this repository is ever made public, this is the blocking item**, not the
   code. `documentation/legal/` covers the client-facing side; the internal side
   is the standing decision to keep the graph in and ship only citations.
