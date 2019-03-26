.PHONY: clean virtualenv test docker dist dist-upload

clean:
	find . -name '*.py[co]' -delete

virtualenv:
	virtualenv -p python3.6 --prompt '|> hydra <| ' env
	env/bin/pip install -r requirements-dev.txt
	env/bin/python setup.py develop
	@echo
	@echo "VirtualENV Setup Complete. Now run: source env/bin/activate"
	@echo

test: comply
	python -m pytest \
		-v \
		--cov=hydra \
		--cov-report=term \
		--cov-report=html:coverage-report \
		tests/

comply:
	safety check
	prospector

docker: clean
	docker build -t hydra:latest .

dist: clean
	rm -rf dist/*
	python setup.py sdist
	python setup.py bdist_wheel

dist-upload:
	twine upload dist/*

install:
	pip install -r requirements.txt
	python setup.py install
