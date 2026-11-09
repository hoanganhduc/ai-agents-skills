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

selftest:

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills/deep-research-workflow/run_deep_research_workflow.ps1" selftest

PowerShell command target:

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills\deep-research-workflow\run_deep_research_workflow.ps1" selftest
