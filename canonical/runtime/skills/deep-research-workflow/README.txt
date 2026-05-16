Minimal runtime helper for initializing a deep-research scaffold.

Examples:

doctor:

  & "$env:AAS_RUNTIME_ROOT\run_skill.bat" `
    "skills\deep-research-workflow\run_deep_research_workflow.bat" doctor

init:

  & "$env:AAS_RUNTIME_ROOT\run_skill.bat" `
    "skills\deep-research-workflow\run_deep_research_workflow.bat" init --dir C:\path\to\workspace
