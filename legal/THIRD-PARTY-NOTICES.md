# Third-Party Notices

GnosisLab includes and references third-party components that are licensed
separately from GnosisLab. These components remain under their respective
licenses.

Unless otherwise stated, original GnosisLab code and documentation are
licensed under the Apache License 2.0. Third-party components are not
relicensed under the GnosisLab Apache-2.0 license.

This file is provided for attribution and license-notice purposes. It is not a
substitute for the full license texts included with each third-party
component.

## Data format and vocabulary standards

The provenance export (`gnosislab export --format ro-crate`) emits documents
that conform to the following published standards. GnosisLab *references*
these formats — it resolves context and vocabulary terms by URI and declares
conformance via `conformsTo` — and does not reproduce or bundle the
specification text or vocabulary definition files of any standard. Emitting
conforming data is the intended use of each standard and requires no license
grant; the entries below are kept here for attribution and clarity.

### RO-Crate Metadata Specification 1.3

- Specification: <https://www.researchobject.org/ro-crate/specification/1.3/>
- JSON-LD context referenced: `https://w3id.org/ro/crate/1.3/context`
- License: **Apache License 2.0**
- Copyright: University of Technology Sydney, The University of Manchester,
  and RO-Crate contributors
- Used by: `gnosislab_export/ro_crate/`

RO-Crate specifications and documentation are Apache-2.0, the same license
as GnosisLab. If a future release ever vendors the `ro-crate-context.json`
file or reproduces specification text, the Apache-2.0 attribution and
license-text requirements apply to that bundled copy.

### JSON-LD 1.1

- Specification: <https://www.w3.org/TR/json-ld11/>
- License: **W3C Document License**; royalty-free patent commitments under
  the W3C Patent Policy
- Copyright: W3C / JSON-LD 1.1 W3C Working Group

### W3C PROV (PROV-O, PROV-JSON)

- Specifications: <https://www.w3.org/TR/prov-o/>,
  <https://www.w3.org/TR/prov-json/>
- License: **W3C Document License**; royalty-free patent commitments under
  the W3C Patent Policy
- Copyright: W3C / PROV Working Group
- Used by: `gnosislab_export/prov/` (namespace terms referenced by URI)

### schema.org vocabulary

- Vocabulary: <https://schema.org/>
- License: **Creative Commons Attribution-ShareAlike 3.0** (CC BY-SA 3.0)
- Copyright: schema.org Community Group / W3C
- Used by: `gnosislab_export/ro_crate/mapping.py` (term URIs such as
  `http://schema.org/CompletedActionStatus` are referenced, not copied)

Schema.org invites use of its term URIs in published data. Reproducing the
vocabulary definition files themselves would trigger the CC BY-SA 3.0
attribution and share-alike terms.

## Runtime and build dependencies

Python package dependencies declared in `pyproject.toml` / `uv.lock` retain
their own licenses (predominantly MIT, BSD, and Apache-2.0). Their license
texts ship inside each installed distribution under
`site-packages/*.dist-info/licenses/`.

## Bundled assets

Any third-party fonts, icons, or similar assets vendored directly into this
repository carry their own license files alongside the asset.
