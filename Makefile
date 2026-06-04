.PHONY: check-specs
check-specs:  ## Verify the repo conforms to /specs
	uv run python scripts/check_specs.py

.PHONY: help
help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-16s %s\n",$$1,$$2}'
