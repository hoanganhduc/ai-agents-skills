.PHONY: doctor precheck audit-system openclaw-inventory openclaw-dry-run-manifest openclaw-approve-manifest openclaw-apply-manifest openclaw-uninstall-manifest openclaw-record-evidence openclaw-validate-evidence openclaw-persistence-check plan install verify smoke rollback uninstall fake-root-lifecycle lifecycle-test list-skills docs docs-site sanitize-check test

ARGS ?=

doctor:
	./installer/bootstrap.sh doctor $(ARGS)

precheck:
	./installer/bootstrap.sh precheck $(ARGS)

audit-system:
	./installer/bootstrap.sh audit-system $(ARGS)

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

list-skills:
	./installer/bootstrap.sh list-skills

docs:
	./installer/bootstrap.sh generate-docs

docs-site:
	sphinx-build -b html docs/source docs/_build/html

sanitize-check:
	./installer/bootstrap.sh --run-python tools/sanitization_check.py
	./installer/bootstrap.sh --run-python -m unittest discover -s tests -p 'test_sanitization.py' -v

test:
	./installer/bootstrap.sh --run-python -m unittest discover -s tests -v
