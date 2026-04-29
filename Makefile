.PHONY: doctor precheck plan install verify rollback uninstall list-skills docs docs-site sanitize-check test

ARGS ?=

doctor:
	./installer/bootstrap.sh doctor $(ARGS)

precheck:
	./installer/bootstrap.sh precheck $(ARGS)

plan:
	./installer/bootstrap.sh plan $(ARGS)

install:
	./installer/bootstrap.sh install $(ARGS)

verify:
	./installer/bootstrap.sh verify $(ARGS)

rollback:
	./installer/bootstrap.sh rollback $(ARGS)

uninstall:
	./installer/bootstrap.sh uninstall $(ARGS)

list-skills:
	./installer/bootstrap.sh list-skills

docs:
	./installer/bootstrap.sh generate-docs

docs-site:
	sphinx-build -b html docs/source docs/_build/html

sanitize-check:
	PYTHONPATH=. python tools/sanitization_check.py
	PYTHONPATH=. python -m unittest discover -s tests -p 'test_sanitization.py' -v

test:
	PYTHONPATH=. python -m unittest discover -s tests -v
