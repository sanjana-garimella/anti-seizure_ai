# aim2_engine - developer commands
# (these targets just wrap the project's own python entrypoints.)

.PHONY: run test demo verify verify-quiet

run:                 ## start the doctor + family web UI (127.0.0.1:8000)
	python3 clinic.py

test:                ## full unittest suite
	python3 -m unittest discover -s tests

demo:                ## decision-engine demo
	python3 demo.py

verify:              ## build-time machine verification of ALL rule tables + safety net
	python3 verification/verify_all.py

verify-quiet:        ## verify, summary only (for CI / pre-commit)
	python3 verification/verify_all.py --quiet
