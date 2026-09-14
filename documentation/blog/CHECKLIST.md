# Writer checklist (run on every file)

Mark each item. A post that fails any row is not ready.

## File and frontmatter

- [ ] Path is `documentation/blog/es/<slug>.md` or `en/<slug>.md`
- [ ] `slug:` equals the filename
- [ ] `lang:` equals the folder
- [ ] `pair:` matches the brief
- [ ] `title`, `description` (<=160 chars), `date`, `updated`, `query`, `answer`
- [ ] `answer` is 40–80 words and is the **first body paragraph** after H1
- [ ] `cta` in {demo, diagnostico, both}; `offer` is a real `webapp.offers` slug
- [ ] `sources` has >=1 entry; every `kind: code` path exists in the repo
- [ ] `faq` has >=3 items; body `## FAQ` matches them
- [ ] `noindex` is false for v1 pillars

## GEO / quality

- [ ] 900–1,400 words of body
- [ ] First screen answers the `query` without a story intro
- [ ] Method names real columns / formula from the cited file
- [ ] Compared-to section uses the **language-specific** set in the brief
- [ ] One CTA; no newsletter; no prices typed in the body
- [ ] Illustrative numbers labelled `ej.` / `e.g.` / `estimado` / `illustrative`

## §10 denylist (fail if present)

- [ ] No "IA de última generación" / "state-of-the-art AI" / "AI that predicts the future"
- [ ] No "autonomía total" / "fully autonomous" / "no human in the loop"
- [ ] No autonomy percentage (82%, 75–80%, …)
- [ ] No "Kern saves X hours" as a hard figure
- [ ] No client success $ or testimonials
- [ ] No Shopify/Amazon inventory integration claim
- [ ] No "integramos tu inventario de Mercado Libre"
- [ ] No "CEO dashboard" / "Kern decides for you" as a shipped product
- [ ] No "replaces your analysts"

## Pairing

- [ ] The other language file exists (except `stocky-alt` EN)
- [ ] Same `pair` id; **different** slug, title, query, examples
- [ ] Not a sentence-by-sentence translation
