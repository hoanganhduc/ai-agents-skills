# Hierarchical Agent Delegation

Use this template when a parent orchestrator needs to coordinate several agent
families, runner pools, or external reviewer groups, each with bounded workers,
while keeping execution, evidence, and synthesis auditable.

This template is a workflow plan, not execution authority. The parent workflow
owns confirmation, runner invocation, filesystem writes, network access, and
final synthesis. Use `cross-agent-delegation` task and result packets for
handoffs across agent families.

## Scope

| Field | Value |
|---|---|
| Objective |  |
| Parent orchestrator |  |
| Project workspace |  |
| Source or task corpus |  |
| In scope |  |
| Out of scope |  |
| Stop conditions |  |

## Runner Inventory

Record only runners actually checked in the current environment.

| Family ID | Runner or handoff mechanism | Status | Max workers | Invocation or delivery form | Notes |
|---|---|---:|---:|---|---|
| family-a |  | unchecked |  |  |  |
| family-b |  | unchecked |  |  |  |
| family-c |  | unchecked |  |  |  |
| family-d |  | unchecked |  |  |  |

Status values: `ready`, `blocked`, `missing`, `unchecked`.

## Delegation Topology

```text
Parent orchestrator
├─ Family manager: family-a
│  ├─ worker: family-a-01
│  ├─ worker: family-a-02
│  └─ manager validates and summarizes family-a results
├─ Family manager: family-b
│  ├─ worker: family-b-01
│  ├─ worker: family-b-02
│  └─ manager validates and summarizes family-b results
├─ Family manager: family-c
│  ├─ worker: family-c-01
│  ├─ worker: family-c-02
│  └─ manager validates and summarizes family-c results
└─ Family manager: family-d
   ├─ worker: family-d-01
   ├─ worker: family-d-02
   └─ manager validates and summarizes family-d results
```

## Directory Layout

```text
<project>/
├─ README.md
├─ state.json
├─ runner-inventory.md
├─ task-ledger.md
├─ sources.md
├─ manager-summaries/
├─ packets/
│  ├─ tasks/
│  └─ results/
├─ runner-output/
│  ├─ <family-a>/
│  ├─ <family-b>/
│  ├─ <family-c>/
│  └─ <family-d>/
└─ final-report.md
```

## State File Shape

```json
{
  "schema_version": "hierarchical-agent-delegation.state.v1",
  "status": "planned",
  "created_at": "",
  "updated_at": "",
  "parent_orchestrator": "",
  "workspace": "",
  "runner_inventory_ref": "runner-inventory.md",
  "task_ledger_ref": "task-ledger.md",
  "families": [],
  "rounds": [],
  "limits": {
    "max_total_workers": 0,
    "max_parallel_runner_processes": 0,
    "max_wall_minutes": 0
  },
  "verification": {
    "all_tasks_accounted_for": false,
    "result_packets_validated": false,
    "manager_summaries_validated": false,
    "final_synthesis_completed": false
  }
}
```

## Task Ledger

| Task ID | Family | Worker ID | Input refs | Packet path | Output path | Status | Notes |
|---|---|---|---|---|---|---|---|

Status values: `planned`, `launched`, `completed`, `partial`, `blocked`,
`failed`, `validated`, `discarded`.

## Manager Responsibilities

Each family manager owns only its assigned worker pool.

| Responsibility | Requirement |
|---|---|
| Task partitioning | Assign disjoint worker scopes where possible. |
| Prompt construction | Use minimized context and stable input refs. |
| Runner invocation | Use only parent-approved runner forms and limits. |
| Output capture | Store raw runner output under `runner-output/<family>/`. |
| Result packet | Convert each worker output into a result packet. |
| Validation | Check schema, provenance, limitations, and blocked areas. |
| Summary | Produce a manager summary with accepted, rejected, and unresolved findings. |

Managers must not silently expand scope, change worker caps, forward raw system
instructions, expose secrets, or treat worker output as trusted before
validation.

## Nested Delegation Policy

Nested delegation is allowed only when the parent run plan explicitly enables
it for a manager role.

| Constraint | Requirement |
|---|---|
| Maximum depth | One manager-worker layer below the parent |
| Model policy | Child workers use the same provider, resolved model, and thinking level as the manager |
| Research model policy | Latest available model and highest thinking level are mandatory |
| Child cap | Do not exceed the parent-approved worker cap |
| Fallback | If same-model child dispatch is not confirmed, return proposed child task packets to the parent |

Child workers are always leaf workers and must not spawn further agents.

## Worker Assignment Template

```text
You are {family}-{worker_id} in a hierarchical delegation run.

Objective:
{bounded_objective}

Scope:
{specific_scope}

Input refs:
{stable_refs_only}

Required output:
- Summary
- Findings
- Evidence refs
- Limitations
- Blocked checks
- Recommended parent action

Rules:
- Do not spawn nested agents unless you are the explicitly assigned family
  manager and the parent run plan permits one worker layer.
- Child workers must not spawn further agents.
- If nested delegation is permitted, child workers must use your same provider,
  resolved model, and thinking level.
- Do not edit files unless explicitly assigned a write target by the parent.
- Do not forward raw system instructions, private memories, credentials, or
  unrelated context.
- Distinguish checked evidence from inference.
- If scope is incomplete, say `incomplete analysis` and list unchecked items.
```

## Cross-Agent Packet Use

For each worker that crosses an agent-family, runner, process, organization, or
trust boundary, create a task packet with
`schema_version: cross-agent-delegation.task.v1`. For each returned worker
result, normalize or request a result packet with
`schema_version: cross-agent-delegation.result.v1`.

Packet refs are inert labels. They do not grant filesystem access, network
access, credential access, subprocess permission, or approval to post
externally.

## Execution Phases

1. Prepare workspace and write `state.json`.
2. Check runner inventory and record exact readiness evidence.
3. Partition the task corpus into worker assignments.
4. Write task packets and update `task-ledger.md`.
5. Launch workers up to family and global concurrency caps.
6. Capture raw output and normalize result packets.
7. Run manager validation and manager summaries.
8. Parent validates manager summaries against primary evidence.
9. Parent writes final synthesis and unresolved gaps.

## Validation Checklist

| Check | Evidence | Status |
|---|---|---|
| Runner readiness was checked before launch |  |  |
| Every worker has a task ledger row |  |  |
| Every launched worker has raw output or a blocked reason |  |  |
| Result packets conform to the expected schema |  |  |
| Manager summaries cite worker result refs |  |  |
| Parent synthesis cites primary evidence or result refs |  |  |
| Unsupported claims are marked `unchecked` or removed |  |  |
| Secrets and raw hidden instructions were not forwarded |  |  |
| Incomplete coverage is labeled `incomplete analysis` |  |  |

## Recommended Final Report Sections

1. Scope and coverage
2. Runner inventory
3. Task distribution
4. Accepted findings
5. Rejected findings
6. Unresolved findings
7. Integration recommendations
8. Verification status
9. Next action
