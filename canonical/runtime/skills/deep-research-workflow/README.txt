Minimal runtime helper for initializing and validating a deep-research scaffold.

Examples:

doctor:

  & "$env:AAS_RUNTIME_ROOT\run_skill.bat" `
    "skills\deep-research-workflow\run_deep_research_workflow.bat" doctor

init:

  & "$env:AAS_RUNTIME_ROOT\run_skill.bat" `
    "skills\deep-research-workflow\run_deep_research_workflow.bat" init --dir C:\path\to\workspace

structured init:

  & "$env:AAS_RUNTIME_ROOT\run_skill.bat" `
    "skills\deep-research-workflow\run_deep_research_workflow.bat" init --structured --dir C:\path\to\workspace

validate:

  & "$env:AAS_RUNTIME_ROOT\run_skill.bat" `
    "skills\deep-research-workflow\run_deep_research_workflow.bat" validate --dir C:\path\to\workspace\research

selftest:

  & "$env:AAS_RUNTIME_ROOT\run_skill.bat" `
    "skills\deep-research-workflow\run_deep_research_workflow.bat" selftest

PowerShell command target:

  & "$env:AAS_RUNTIME_ROOT\run_skill.ps1" `
    "skills\deep-research-workflow\run_deep_research_workflow.ps1" selftest
