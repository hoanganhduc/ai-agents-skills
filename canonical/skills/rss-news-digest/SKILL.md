---
name: rss-news-digest
description: Use when the user wants RSS-based research/news digests, feed management, or feed health checks.
metadata:
  short-description: RSS digests and feed management
---

# RSS News Digest

## Base path

- `~/.codex/runtime/workspace/skills/rss-news-digest/`

Use the Codex runtime runner rather than invoking the RSS script directly.

Shared runner:

- `bash ~/.codex/runtime/run_skill.sh`

## Use cases

- get the research RSS digest
- get jobs/events/general/video digests
- list/search/add/edit/disable feeds
- run feed doctor/health checks

## Core execution

```bash
bash ~/.codex/runtime/run_skill.sh skills/rss-news-digest/run_rss_news_digest.sh <COMMAND AND ARGS>
```

## Common actions

- `run --tag research`
- `run --all-tags`
- `run --tag jobs --max-items 20 --per-feed-limit 5`
- `list-feeds`
- `add-feed "<URL>" --tag research --priority 5`
- `edit-feed "<URL>" --tag research --priority 5`
- `disable-feed "<URL>"` / `enable-feed "<URL>"`
- `remove-feed "<URL>"`
- `backup-feeds --reason "REASON"`
- `list-backups`
- `restore-feeds-backup <backup-name-or-path>`
- `export-feeds-tsv --output /tmp/feeds.tsv`
- `import-feeds-tsv /tmp/feeds.tsv`
- `doctor`
- `search-feeds "<query>"`

Verified example shapes:

```bash
bash ~/.codex/runtime/run_skill.sh skills/rss-news-digest/run_rss_news_digest.sh run --tag research --max-items 25 --per-feed-limit 5
```

```bash
bash ~/.codex/runtime/run_skill.sh skills/rss-news-digest/run_rss_news_digest.sh add-feed "https://example.com/rss.xml" --tag research --priority 5
```

## After execution

If a digest is produced, read the digest path reported by the command output and summarize the top items for the user.
