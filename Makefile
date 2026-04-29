.PHONY: doctor plan install verify rollback uninstall list-skills docs test

ARGS ?=

doctor:
	./installer/bootstrap.sh doctor $(ARGS)

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

test:
	PYTHONPATH=. python -m unittest discover -s tests -v
