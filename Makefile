.PHONY: doctor precheck audit-system plan install verify smoke rollback uninstall fake-root-lifecycle lifecycle-test list-skills docs docs-site sanitize-check test

ARGS ?=

doctor:
	./installer/bootstrap.sh doctor $(ARGS)

precheck:
	./installer/bootstrap.sh precheck $(ARGS)

audit-system:
	./installer/bootstrap.sh audit-system $(ARGS)

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
