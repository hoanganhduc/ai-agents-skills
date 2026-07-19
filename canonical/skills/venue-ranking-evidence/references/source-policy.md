# Source Policy

## Source classes

- `official-current`: the responsible authority exposes a current edition,
  update marker, API, export, or live record.
- `official-historical`: an official edition is valid for a named past period.
- `secondary-legacy`: an aggregator or retired list reports older material.
- `unverified`: authority, edition, or provenance is not established.

Only an observation from an `official-current` source whose own
`freshness_status` is `verified-current` may satisfy a request for latest
official status. In the current runtime, only a successful live ICORE
discovery/export can produce that state. Retrieval time, an import filename, a
user-supplied year, or descriptor policy alone is not a current-edition signal.

## Assertion kinds

Keep these independent:

- `rank` or `classification-level`
- `quartile`
- `metric`
- `index-membership`
- `collection-coverage`
- `coverage-status`

Record the scheme for every rank or quartile, such as ICORE class, CCF class,
SJR quartile, CiteScore quartile, JIF quartile, or JUFO level.

## Built-in source intent

| Source ID | Authority and primary assertions | Runtime data path | Proof/currentness capability |
|---|---|---|---|
| `icore` | official conference class and FoR | built-in public live discovery/export; validated offline cache | only reviewed live/currentness/proof path; offline formerly-current cache rows are unconfirmed |
| `ccf` | official CS conference/journal A-B-C class | authorized normalized CSV/JSON import only; public/manual gate may apply | no built-in live query, latest verifier, or proof |
| `scimago` | official SJR portal; SJR and category/year quartile | authorized normalized CSV/JSON import only; browser gate may apply | no built-in live query, latest verifier, or proof |
| `scopus` | official membership, coverage, and CiteScore metrics | authorized normalized CSV/JSON import only; licensed API/UI may apply | no built-in live query, latest verifier, or proof |
| `wos-mjl` | official WoS journal collection membership | authorized normalized CSV/JSON import only; login may apply | no built-in live query, latest verifier, or proof |
| `clarivate-jcr` | official JIF/JCI category ranks and quartiles | authorized normalized CSV/JSON import only; subscription applies | no built-in live query, latest verifier, or proof |
| `jufo` | official national classification level | authorized normalized CSV/JSON import only | no built-in live query, latest verifier, or proof |
| `norwegian-register` | official national publication-channel level | authorized normalized CSV/JSON import only | no built-in live query, latest verifier, or proof |
| `doaj` | official open-access index membership | authorized normalized CSV/JSON import only | no built-in live query, latest verifier, or proof |
| `conference-ranks` | secondary-legacy historical aggregator display | authorized normalized CSV/JSON import only | never latest; no built-in live query, latest verifier, or proof |

For ICORE's built-in live path, discover the edition at runtime. Do not hardcode
a previously observed edition or treat an offline cache as permanently latest.

Only `icore` has a reviewed built-in live bulk parser, edition-freshness
adapter, and proof association adapter. For the other nine built-in IDs, the
registry is an authority/access contract: lookup accepts a normalized,
authorized CSV/JSON interchange, not an arbitrary raw provider export. Every
such import remains `currentness-unconfirmed`, even when the descriptor says
`may_claim_latest: true`. Raw layouts require a reviewed declarative descriptor
and explicit field mapping. Report `incomplete analysis` when a requested source
was not actually imported or queried.

## Freshness

Record separately:

- `edition` or `metric_year`;
- `data_as_of` or effective date;
- official page/list update or release date when available;
- `retrieved_at` and `checked_at` in UTC;
- parser version and raw response hash.

Allowed delivery states are `verified-current`, `verified-historical`, `stale`,
`currentness-unconfirmed`, and `blocked`.

## Live access

Built-in live network access currently exists only for ICORE. The other nine
source IDs require an authorized normalized `--data-file`; network/source gates
do not create a live adapter for them.

Require `--allow-network` and each `--allow-source`. Apply official-domain
allowlists, HTTPS, bounded time/bytes/pages, conservative retries, and explicit
rate limits. Prefer official exports and documented APIs over HTML scraping.
Record 403, 429, login, WAF, CAPTCHA, parser drift, and missing edition as
blocked or partial states without falling back silently to a third party.

## Declarative source extension

User-added sources use `venue-ranking-source.v1` and an `adapter` of `csv` or
`json`. Map semantic fields to input columns without executable hooks:

```json
{
  "schema_version": "venue-ranking-source.v1",
  "source_id": "society-list",
  "display_name": "Society List",
  "official_domains": ["rankings.example.org"],
  "may_claim_latest": false,
  "lookup": {
    "adapter": "csv",
    "field_mapping": {
      "canonical_title": "title",
      "aliases": "acronym",
      "issn": "issn",
      "assertion_kind": "kind",
      "scheme": "scheme",
      "category": "category",
      "value": "value",
      "metric_year": "year",
      "official_url": "official_url"
    }
  }
}
```

`canonical_title` and `value` mappings are required. Supported optional mappings
are `venue_id`, `venue_type`, `aliases`, `issn`, `eissn`, `provider_id`,
`assertion_kind`, `scheme`, `category`, `collection`, `edition`, `metric_year`,
and `official_url`. Official URLs must be credential-free HTTPS URLs under the
descriptor allowlist. Imports are hash-recorded and labelled
`currentness-unconfirmed`; no current declarative descriptor can enable live
lookup, latest verification, or proof.

The ICORE cache preserves the complete live discovery attestation: official
discovery/export URLs and domains, discovery response hash and edition signal,
export hash/size, retrieval timestamp, and original live freshness state. An
offline cache read cannot establish that the discovered edition is still
current: rows from the cache's formerly current edition become
`currentness-unconfirmed`. Rows whose own edition was historical remain
`verified-historical`. Future timestamps, mismatched row editions, missing
access attestations, and inconsistent hashes/domains fail closed; cache age or
file names can never create currentness.
