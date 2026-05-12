# Claude Target

Generated Claude artifacts use canonical skill names. Legacy slash commands can
remain as aliases, but the installed skill folder and `SKILL.md` frontmatter
should use the canonical name.

Zotero and Calibre integrations must not maintain separate path assumptions in
Claude commands or skills. They should call the shared profile-aware wrappers
and defer library authority decisions to the generated local-library profile.
