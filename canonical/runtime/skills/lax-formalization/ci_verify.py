#!/usr/bin/env python3
"""Trusted CI entrypoint; untrusted project configuration is data, not a script."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from lax_formalization import read_json, regular_bytes, safe_name, tree_inventory
from lax_executor import verify, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--dependency-requests", type=Path, help="operator-provided archive-ID/request mapping outside the candidate")
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("LAX_HOME", str(Path.home()/'.lax'))) / 'lax-database')
    args = parser.parse_args()
    project = args.project.resolve(); state = args.state_dir.resolve()
    if state.exists() or project == state or project in state.parents: raise ValueError("CI state must be new and outside candidate")
    scope = read_json(project / '.lax-targets.json')
    if set(scope) != {"submission", "environment", "targets"}: raise ValueError("invalid CI scope fields")
    if scope['submission'] != '.': safe_name(scope['submission'])
    dependencies = {}
    if args.dependency_requests:
        mapping = args.dependency_requests.resolve()
        if mapping == project or project in mapping.parents: raise ValueError('dependency requests must be operator-owned outside candidate')
        dependencies = read_json(mapping)
    concept = project / scope['submission'] / 'concepts'
    inventory = tree_inventory(concept)
    state.mkdir(parents=True, mode=0o700); challenge = state/'challenge';challenge.mkdir()
    for entry in inventory['files']:
        dest=challenge/entry['path'];dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(regular_bytes(concept/entry['path']))
    if tree_inventory(challenge) != inventory:raise ValueError('concept snapshot changed')
    request={'schema_version':'lax-request.v1','project_root':str(project),'submission':scope['submission'],
             'database_root':str(args.database.resolve()),'environment':scope['environment'],
             'targets':scope['targets'],'challenge_root':str(challenge),'dependencies':dependencies}
    write_json(state/'request.json',request)
    result=verify(state/'request.json',state/'evidence')
    print(json.dumps({'status':result['status'],'machine_status':result['machine_status'],
                      'closure_status':result['closure_status'],'semantic_status':result['semantic_status'],
                      'source_commit':result['source']['commit'],'publication_enabled':False},indent=2))
    return 0 if result['status']=='passed' else 1


if __name__=='__main__':raise SystemExit(main())
