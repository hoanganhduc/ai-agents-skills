.PHONY: help doctor precheck audit-system library-profile-audit openclaw-inventory openclaw-dry-run-manifest openclaw-approve-manifest openclaw-apply-manifest openclaw-uninstall-manifest openclaw-record-evidence openclaw-validate-evidence openclaw-persistence-check plan install verify smoke rollback uninstall fake-root-lifecycle lifecycle-test runtime-smoke runtime-inventory delegate-agent validate-delegation-packet list-skills list-artifacts describe describe-artifact docs generate-docs docs-site sanitize-check test

ARGS ?=

help:
	./installer/bootstrap.sh help $(ARGS)

doctor:
	./installer/bootstrap.sh doctor $(ARGS)

precheck:
	./installer/bootstrap.sh precheck $(ARGS)

audit-system:
	./installer/bootstrap.sh audit-system $(ARGS)

library-profile-audit:
	./installer/bootstrap.sh library-profile-audit $(ARGS)

openclaw-inventory:
	./installer/bootstrap.sh openclaw-inventory $(ARGS)

openclaw-dry-run-manifest:
	./installer/bootstrap.sh openclaw-dry-run-manifest $(ARGS)

openclaw-approve-manifest:
	./installer/bootstrap.sh openclaw-approve-manifest $(ARGS)

openclaw-apply-manifest:
	./installer/bootstrap.sh openclaw-apply-manifest $(ARGS)

openclaw-uninstall-manifest:
	./installer/bootstrap.sh openclaw-uninstall-manifest $(ARGS)

openclaw-record-evidence:
	./installer/bootstrap.sh openclaw-record-evidence $(ARGS)

openclaw-validate-evidence:
	./installer/bootstrap.sh openclaw-validate-evidence $(ARGS)

openclaw-persistence-check:
	./installer/bootstrap.sh openclaw-persistence-check $(ARGS)

plan:
	./installer/bootstrap.sh plan $(ARGS)

install:
	./installer/bootstrap.sh install $(ARGS)

verify:
	./installer/bootstrap.sh verify $(ARGS)

smoke:
	./installer/bootstrap.sh smoke $(ARGS)

rollback:
	./installer/bootstrap.sh rollback $(ARGS)

uninstall:
	./installer/bootstrap.sh uninstall $(ARGS)

fake-root-lifecycle:
	./installer/bootstrap.sh fake-root-lifecycle $(ARGS)

lifecycle-test:
	./installer/bootstrap.sh lifecycle-test $(ARGS)

runtime-smoke:
	./installer/bootstrap.sh runtime-smoke $(ARGS)

runtime-inventory:
	./installer/bootstrap.sh runtime-inventory $(ARGS)

delegate-agent:
	./installer/bootstrap.sh delegate-agent $(ARGS)

validate-delegation-packet:
	./installer/bootstrap.sh validate-delegation-packet $(ARGS)

list-skills:
	./installer/bootstrap.sh list-skills $(ARGS)

list-artifacts:
	./installer/bootstrap.sh list-artifacts $(ARGS)

describe:
	./installer/bootstrap.sh describe $(ARGS)

describe-artifact:
	./installer/bootstrap.sh describe-artifact $(ARGS)

docs:
	./installer/bootstrap.sh generate-docs $(ARGS)

generate-docs: docs

docs-site:
	./installer/bootstrap.sh --run-python -c 'import importlib.util, sys; missing = [m for m in ("sphinx", "myst_parser", "sphinx_rtd_theme") if importlib.util.find_spec(m) is None]; sys.exit("Install docs dependencies first: python -m pip install -r docs/requirements.txt; missing: " + ", ".join(missing) if missing else 0)'
	./installer/bootstrap.sh --run-python -m sphinx -b html docs/source docs/_build/html

sanitize-check:
	./installer/bootstrap.sh --run-python tools/sanitization_check.py
	./installer/bootstrap.sh --run-python -m unittest discover -s tests -p 'test_sanitization.py' -v

test:
	./installer/bootstrap.sh --run-python -m unittest discover -s tests -v
