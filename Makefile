PYTHON ?= python3.12
VENV = .venv/bin/python
MODEL ?= glm-5.3

.PHONY: setup test demo-prepare demo-check demo demo-llm demo-ml demo-smoke demo-smoke-llm
setup:
	$(PYTHON) -m venv .venv
	$(VENV) -m pip install -r web/requirements.txt pytest
	$(VENV) tools/check_env.py
test:
	$(VENV) demo/local.py test
	node tests/test_dom_helper.cjs
demo-prepare:
	$(VENV) demo/local.py prepare
demo-check:
	$(VENV) demo/local.py check
demo:
	$(VENV) demo/local.py run
demo-llm:
	$(VENV) demo/local.py run --llm --model "$(MODEL)"
demo-ml:
	$(VENV) demo/local.py ml
demo-smoke:
	$(VENV) demo/local.py smoke
demo-smoke-llm:
	$(VENV) demo/local.py smoke --llm --model "$(MODEL)"
