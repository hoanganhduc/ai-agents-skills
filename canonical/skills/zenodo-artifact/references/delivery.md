# Metadata and later delivery

Use `upload_type: software`, a nonempty title/description/version/license and a
nonempty `creators` list with names. Resolve all template placeholders before
packaging. A `CITATION.cff` file is useful for GitHub citation display. Zenodo
uses `.zenodo.json` in preference to CFF when both exist, so compare their title,
version, license and credit. Use a full CFF validator for schema-level checks;
the stdlib helper's scalar/creator consistency checks do not replace that.
The helper accepts the template's JSON-compatible YAML CFF form and refuses
other YAML encodings whose author structure it cannot validate.

Do not assume GitHub release attachments or CI artifacts are automatically
archived. Prefer an explicit reviewed bundle when preserving verification
reports. CI artifacts have retention limits. The default bundle does not contain
`.git`, personal logs, credentials or large machine caches.

Publication is outside this runtime. A later authorized manual upload can use
the reviewed files and metadata. For an API implementation, the official
deposition flow is create draft, upload files via the returned bucket link,
update metadata, verify the draft inventory, and publish in a separate action.
Use bearer headers; do not put credentials in URLs. Sandbox is also an external
service and does not remove the need for authorization.

Once GitHub integration is enabled, a new Release can trigger publication.
Checks on a release event are therefore not a pre-publication gate. Verify the
exact tag's commit and obtain the permitted publication scope before creating it.

Current management guides describe limited post-publication correction/deletion
windows while some older API text states stronger immutability. Do not design
rollback around deleting a publication. Recheck the service policy at the time.

Sources: [Zenodo metadata](https://help.zenodo.org/docs/github/describe-software/zenodo-json/),
[GitHub integration](https://help.zenodo.org/docs/github/enable-repository/),
[API](https://developers.zenodo.org/),
[file management](https://help.zenodo.org/docs/deposit/manage-files/).
