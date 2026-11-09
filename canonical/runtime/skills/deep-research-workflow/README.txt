Minimal runtime helper for initializing and validating a deep-research scaffold.

Examples:

doctor:

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills/deep-research-workflow/run_deep_research_workflow.ps1" doctor

init:

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills/deep-research-workflow/run_deep_research_workflow.ps1" init --dir C:\path\to\workspace

structured init:

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills/deep-research-workflow/run_deep_research_workflow.ps1" init --structured --dir C:\path\to\workspace

validate:

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills/deep-research-workflow/run_deep_research_workflow.ps1" validate --dir C:\path\to\workspace\research

delivery-check (validate, then READY only for a finalizable decision, promoted required formal targets and unchanged inputs):

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills/deep-research-workflow/run_deep_research_workflow.ps1" delivery-check --dir C:\path\to\workspace\research

add-computation-evidence (copy a graph-verifier or Sage result into the run and append one computation row for a claim; exit 1 refuses and writes nothing):

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills/deep-research-workflow/run_deep_research_workflow.ps1" add-computation-evidence --dir C:\path\to\workspace\research --result C:\path\to\result.json --claim-id C1 --relation asserts

selftest:

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills/deep-research-workflow/run_deep_research_workflow.ps1" selftest

PowerShell command target:

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills\deep-research-workflow\run_deep_research_workflow.ps1" selftest
