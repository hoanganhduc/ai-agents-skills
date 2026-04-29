# Source Handoff

Carry source information from search to analysis using a compact structure:

```text
source_id:
title:
url:
date:
source_type:
key_facts:
relevant_claims:
confidence:
```

Rules:

- keep one source record per distinct source
- link major claims back to source ids
- mark uncertainties instead of smoothing them away

When a post-analysis figure is warranted, create a separate `figure-brief.json` rather than mixing figure instructions into source records.

Minimum figure brief fields:

```text
figure_id:
title:
purpose:
source_ids:
diagram_family:
content_requirements:
layout_constraints:
output_dir:
```

Rules for figure handoff:

- create figure briefs only after analysis identifies a concrete figure need
- preserve the `S*` ids that justify the figure
- assign stable `F*` ids to figure outputs
- keep figure artifacts under a dedicated `figures/` subtree when practical
