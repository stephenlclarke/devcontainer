SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := workflow

DEVCONTAINER_VERSION ?= 0.1.0
SWIFT ?= swift
PYTHON ?= python3
MARKDOWNLINT ?= markdownlint
SWIFTLINT ?= swiftlint
SWIFTFORMAT ?= swiftformat
ACTIONLINT ?= actionlint
SWIFT_COVERAGE_MIN ?= 90
SWIFT_TEST_RESULT_LOG ?= .build/swift-test.log
SWIFT_TEST_ATTEMPTS ?= 2
DOCS_OUTPUT_DIR ?= _site
DOCS_HOSTING_BASE_PATH ?= devcontainer
SWIFT_RESOLVED_FLAGS ?= --disable-automatic-resolution
DIST_DIR ?= dist
PARITY_EVIDENCE_DIR ?= .build/parity
DEVCONTAINER_CLI_VERSION ?= 0.88.0
SONAR_SCAN_ATTEMPTS ?= 3
SONAR_QUALITYGATE_WAIT ?= true

.PHONY: all workflow ci bootstrap resolve build build-release test test-unit
.PHONY: test-contract test-integration swift-test coverage coverage-check
.PHONY: asan tsan test-asan test-tsan check lint format format-check docs
.PHONY: serve-docs parity-manifest parity-docker parity-apple-stock
.PHONY: parity-apple-compose parity parity-vscode parity-release runtime-check
.PHONY: package package-release homebrew-formula release-check sonar sonar-scan clean

all: workflow

workflow: ci

ci: check build

bootstrap:
	Tools/ci/bootstrap.sh

resolve:
	$(SWIFT) package resolve

build:
	$(SWIFT) build $(SWIFT_RESOLVED_FLAGS)

build-release:
	GIT_COMMIT="$$(git rev-parse HEAD)" DEVCONTAINER_BUILD_LANE=release \
		$(SWIFT) build $(SWIFT_RESOLVED_FLAGS) -c release

test: swift-test

test-unit: swift-test

test-contract: swift-test

test-integration:
	DEVCONTAINER_RUN_HOST_INTEGRATION=1 $(MAKE) swift-test

swift-test:
	@mkdir -p .build
	@SWIFT_TEST_RESULT_LOG="$(SWIFT_TEST_RESULT_LOG)" \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		Tools/ci/run-swift-test.sh \
		$(SWIFT) test $(SWIFT_RESOLVED_FLAGS) --no-parallel

coverage:
	@mkdir -p .build
	@SWIFT_TEST_RESULT_LOG=.build/swift-coverage.log \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		SWIFT_TEST_ACCEPT_SIGNAL_13=0 \
		Tools/ci/run-swift-test.sh \
		$(SWIFT) test $(SWIFT_RESOLVED_FLAGS) \
		--scratch-path .build/coverage --enable-code-coverage --no-parallel
	@$(SWIFT) test --scratch-path .build/coverage \
		--show-codecov-path > .build/codecov-path

coverage-check: coverage
	$(PYTHON) Tools/coverage/check-swift-coverage.py \
		--minimum "$(SWIFT_COVERAGE_MIN)" \
		--sonar-output coverage.xml \
		--source-root "$(CURDIR)" \
		"$$(cat .build/codecov-path)"

sonar: coverage-check sonar-scan

sonar-scan:
	@test -s coverage.xml || { \
		printf 'coverage.xml is missing; run make coverage-check before make sonar-scan\n' >&2; \
		exit 2; \
	}
	@sonar_token="$${SONAR_TOKEN:-$${SONAR_TOKEN_PERSONAL:-}}"; \
	if [[ -z "$$sonar_token" ]]; then \
		printf 'SONAR_TOKEN or SONAR_TOKEN_PERSONAL is required for make sonar-scan\n' >&2; \
		exit 2; \
	fi
	@sonar_token="$${SONAR_TOKEN:-$${SONAR_TOKEN_PERSONAL:-}}"; \
	attempt=1; \
	while true; do \
		set +e; \
		SONAR_TOKEN="$$sonar_token" sonar-scanner \
			-Dsonar.qualitygate.wait="$(SONAR_QUALITYGATE_WAIT)"; \
		status="$$?"; \
		set -e; \
		if [[ "$$status" -eq 0 ]]; then \
			exit 0; \
		fi; \
		if (( attempt >= $(SONAR_SCAN_ATTEMPTS) )); then \
			exit "$$status"; \
		fi; \
		printf 'Sonar scanner failed with exit %s; retrying %s/%s after 20 seconds...\\n' \
			"$$status" "$$((attempt + 1))" "$(SONAR_SCAN_ATTEMPTS)" >&2; \
		sleep 20; \
		((attempt += 1)); \
	done

asan:
	@SWIFT_TEST_RESULT_LOG=.build/swift-asan.log \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		Tools/ci/run-swift-test.sh \
		$(SWIFT) test $(SWIFT_RESOLVED_FLAGS) \
		--scratch-path .build/asan --sanitize=address --no-parallel

tsan:
	@SWIFT_TEST_RESULT_LOG=.build/swift-tsan.log \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		Tools/ci/run-swift-test.sh \
		$(SWIFT) test $(SWIFT_RESOLVED_FLAGS) \
		--scratch-path .build/tsan --sanitize=thread --no-parallel

test-asan: asan

test-tsan: tsan

check: format-check lint test coverage-check docs parity-manifest

lint:
	$(PYTHON) -m unittest discover Tools/coverage
	$(PYTHON) -m unittest discover Tools/parity
	$(PYTHON) -m unittest discover Tools/release
	$(PYTHON) -m unittest discover Tools/ci
	$(MARKDOWNLINT) '*.md' 'docs/**/*.md' 'Tests/**/*.md' \
		'Sources/**/*.md'
	@mkdir -p .build
	$(PYTHON) Tools/ci/swiftlint_baseline.py \
		--input .swiftlint-baseline.json \
		--output .build/swiftlint-baseline.json \
		--root "$(CURDIR)"
	$(SWIFTLINT) lint --strict --quiet \
		--baseline .build/swiftlint-baseline.json Sources Tests
	$(SWIFTFORMAT) Sources Tests --lint
	bash -n Tools/ci/*.sh scripts/*.sh
	shellcheck Tools/ci/*.sh scripts/*.sh
	$(ACTIONLINT)

format:
	$(SWIFTFORMAT) Sources Tests

format-check:
	$(SWIFTFORMAT) Sources Tests --lint

parity-manifest:
	$(PYTHON) Tools/parity/validate_manifest.py

parity-docker:
	Tools/parity/run-lane.sh docker "$(PARITY_EVIDENCE_DIR)"

parity-apple-stock:
	Tools/parity/run-lane.sh apple-stock "$(PARITY_EVIDENCE_DIR)"

parity-apple-compose:
	Tools/parity/run-lane.sh apple-compose "$(PARITY_EVIDENCE_DIR)"

parity: parity-docker parity-apple-stock parity-apple-compose
	$(PYTHON) Tools/parity/compare_results.py "$(PARITY_EVIDENCE_DIR)"

parity-vscode:
	Tools/parity/run-vscode.sh "$(PARITY_EVIDENCE_DIR)"

parity-release: parity parity-vscode
	$(PYTHON) Tools/parity/validate_manifest.py --release

runtime-check: test-integration test-asan test-tsan parity-release

package:
	scripts/package.sh

package-release: release-check package

homebrew-formula: package
	@mkdir -p "$(DIST_DIR)"
	$(PYTHON) Tools/release/render-homebrew-formula.py \
		--version "$(DEVCONTAINER_VERSION)" \
		--archive "$(DIST_DIR)/devcontainer-$(DEVCONTAINER_VERSION)-macos-arm64.tar.gz" \
		--template Tools/release/devcontainer.rb.in \
		--output "$(DIST_DIR)/devcontainer.rb"
	ruby -c "$(DIST_DIR)/devcontainer.rb"

release-check: check test-asan test-tsan parity-release homebrew-formula

docs:
	scripts/make-docs.sh "$(DOCS_OUTPUT_DIR)" "$(DOCS_HOSTING_BASE_PATH)"

serve-docs: docs
	$(PYTHON) -m http.server 8000 --directory "$(DOCS_OUTPUT_DIR)"

clean:
	$(PYTHON) Tools/ci/safe-clean.py
