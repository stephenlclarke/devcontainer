SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := workflow

DEVCONTAINER_VERSION ?= 0.1.0
SWIFT ?= swift
SWIFT_STRICT_FLAGS ?= -Xswiftc -warnings-as-errors
PYTHON ?= python3
MARKDOWNLINT ?= markdownlint
SWIFTLINT ?= swiftlint
SWIFTFORMAT ?= swiftformat
ACTIONLINT ?= actionlint
SWIFT_COVERAGE_MIN ?= 90
SWIFT_COVERAGE_CHANGED_MIN ?= 90
SWIFT_COVERAGE_BASE ?=
SWIFT_COVERAGE_HEAD ?= HEAD
SWIFT_TEST_RESULT_LOG ?= .build/swift-test.log
SWIFT_TEST_ATTEMPTS ?= 2
DOCS_OUTPUT_DIR ?= _site
DOCS_HOSTING_BASE_PATH ?= devcontainer
SWIFT_RESOLVED_FLAGS ?= --disable-automatic-resolution
DIST_DIR ?= dist
PARITY_EVIDENCE_DIR ?= .build/parity
DEVCONTAINER_CLI_VERSION ?= 0.88.0
DEVCONTAINER_PACKAGE_LANE ?= development
DEVCONTAINER_PACKAGE_RUN_NUMBER ?=
DEVCONTAINER_SIGNING_REQUIRED ?= 0
SONAR_SCAN_ATTEMPTS ?= 3
SONAR_QUALITYGATE_WAIT ?= true

.PHONY: all workflow ci bootstrap resolve build build-release test test-unit
.PHONY: test-contract test-integration swift-test coverage coverage-check
.PHONY: asan tsan test-asan test-tsan check lint format format-check docs
.PHONY: serve-docs parity-manifest parity-docker parity-apple-stock
.PHONY: parity-apple-compose parity parity-vscode-docker
.PHONY: parity-vscode-apple-stock parity-vscode-apple-compose parity-vscode
.PHONY: parity-release runtime-check
.PHONY: package package-release homebrew-formula homebrew-formula-current
.PHONY: release-version
.PHONY: prepare-release release-check release-gate-hosted sonar sonar-scan clean

all: workflow

workflow: ci

ci: check build

bootstrap:
	Tools/ci/bootstrap.sh

resolve:
	$(SWIFT) package resolve

build:
	$(SWIFT) build $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS)

build-release:
	GIT_COMMIT="$$(git rev-parse HEAD)" DEVCONTAINER_BUILD_LANE=release \
		$(SWIFT) build $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) -c release

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
		$(SWIFT) test $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) --no-parallel

coverage:
	@mkdir -p .build
	@find .build/coverage -type f \
		\( -name '*.profraw' -o -name '*.profdata' -o -name 'devcontainer.json' \) \
		-delete 2>/dev/null || true
	@SWIFT_TEST_RESULT_LOG=.build/swift-coverage.log \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		SWIFT_TEST_ACCEPT_SIGNAL_13=0 \
		Tools/ci/run-swift-test.sh \
		$(SWIFT) test $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--scratch-path .build/coverage --enable-code-coverage --no-parallel
	@$(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
		$(SWIFT_STRICT_FLAGS) \
		--scratch-path .build/coverage --enable-code-coverage \
		--product devcontainer
	@$(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
		$(SWIFT_STRICT_FLAGS) \
		--scratch-path .build/coverage --enable-code-coverage \
		--product devcontainer-compose
	@Tools/coverage/run-cli-coverage.sh \
		"$$($(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
			--scratch-path .build/coverage --show-bin-path)"
	@Tools/coverage/export-swift-coverage.sh \
		"$$($(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
			--scratch-path .build/coverage --show-bin-path)" \
		> .build/codecov-path

coverage-check: coverage
	@coverage_args=( \
		--minimum "$(SWIFT_COVERAGE_MIN)" \
		--changed-minimum "$(SWIFT_COVERAGE_CHANGED_MIN)" \
		--lcov-output coverage.lcov \
		--sonar-output coverage.xml \
		--source-root "$(CURDIR)" \
		--repository "$(CURDIR)" \
	); \
	if [[ -n "$(SWIFT_COVERAGE_BASE)" ]]; then \
		coverage_args+=( \
			--changed-since "$(SWIFT_COVERAGE_BASE)" \
			--head-ref "$(SWIFT_COVERAGE_HEAD)" \
		); \
	fi; \
	$(PYTHON) Tools/coverage/check-swift-coverage.py \
		"$${coverage_args[@]}" \
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
		$(SWIFT) test $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--scratch-path .build/asan --sanitize=address --no-parallel

tsan:
	@SWIFT_TEST_RESULT_LOG=.build/swift-tsan.log \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		Tools/ci/run-swift-test.sh \
		$(SWIFT) test $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
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
	bash -n Tools/ci/*.sh Tools/coverage/*.sh Tools/parity/*.sh \
		Tools/release/*.sh scripts/*.sh
	shellcheck Tools/ci/*.sh Tools/coverage/*.sh Tools/parity/*.sh \
		Tools/release/*.sh scripts/*.sh
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

parity-vscode-docker:
	Tools/parity/run-vscode.sh "$(PARITY_EVIDENCE_DIR)" docker

parity-vscode-apple-stock:
	Tools/parity/run-vscode.sh "$(PARITY_EVIDENCE_DIR)" apple-stock

parity-vscode-apple-compose:
	Tools/parity/run-vscode.sh "$(PARITY_EVIDENCE_DIR)" apple-compose

parity-release: parity parity-vscode
	$(PYTHON) Tools/parity/validate_manifest.py --release

runtime-check: test-integration test-asan test-tsan parity-release

package:
	DEVCONTAINER_PACKAGE_LANE="$(DEVCONTAINER_PACKAGE_LANE)" \
	DEVCONTAINER_PACKAGE_RUN_NUMBER="$(DEVCONTAINER_PACKAGE_RUN_NUMBER)" \
	DEVCONTAINER_SIGNING_REQUIRED="$(DEVCONTAINER_SIGNING_REQUIRED)" \
		scripts/package.sh

package-release: release-check
	$(MAKE) package \
		DEVCONTAINER_PACKAGE_LANE=stable \
		DEVCONTAINER_SIGNING_REQUIRED=1

release-version:
	@test -n "$(VERSION_SELECTOR)" || { \
		printf 'VERSION_SELECTOR is required, for example: make release-version VERSION_SELECTOR=--+\n' >&2; \
		exit 2; \
	}
	$(PYTHON) Tools/release/release-version.py \
		--repository "$(CURDIR)" \
		--selector="$(VERSION_SELECTOR)"

prepare-release:
	@test -n "$(VERSION_SELECTOR)" || { \
		printf 'VERSION_SELECTOR is required, for example: make prepare-release VERSION_SELECTOR=--+\n' >&2; \
		exit 2; \
	}
	$(PYTHON) Tools/release/release-version.py \
		--repository "$(CURDIR)" \
		--selector="$(VERSION_SELECTOR)" \
		--write

homebrew-formula: DEVCONTAINER_PACKAGE_LANE = stable
homebrew-formula: package
	@mkdir -p "$(DIST_DIR)"
	$(PYTHON) Tools/release/render-homebrew-formula.py \
		--product-version "$(DEVCONTAINER_VERSION)" \
		--formula-class Devcontainer \
		--url "https://github.com/stephenlclarke/devcontainer/releases/download/$(DEVCONTAINER_VERSION)/devcontainer-release-arm64.tar.gz" \
		--conflicts-with devcontainer-current \
		--archive "$(DIST_DIR)/devcontainer-release-arm64.tar.gz" \
		--template Tools/release/devcontainer.rb.in \
		--output "$(DIST_DIR)/devcontainer.rb"
	ruby -c "$(DIST_DIR)/devcontainer.rb"

homebrew-formula-current: DEVCONTAINER_PACKAGE_LANE = current
homebrew-formula-current: package
	@test -n "$(DEVCONTAINER_PACKAGE_RUN_NUMBER)" || { \
		printf 'DEVCONTAINER_PACKAGE_RUN_NUMBER is required for a Current formula\n' >&2; \
		exit 2; \
	}
	@formula_version="$$( \
		$(PYTHON) Tools/release/package-context.py \
			--product-version "$(DEVCONTAINER_VERSION)" \
			--lane current \
			--commit "$$(git rev-parse --verify HEAD)" \
			--run-number "$(DEVCONTAINER_PACKAGE_RUN_NUMBER)" \
			--field formulaVersion \
	)"; \
	asset="$$( \
		$(PYTHON) Tools/release/package-context.py \
			--product-version "$(DEVCONTAINER_VERSION)" \
			--lane current \
			--commit "$$(git rev-parse --verify HEAD)" \
			--run-number "$(DEVCONTAINER_PACKAGE_RUN_NUMBER)" \
			--field asset \
	)"; \
	$(PYTHON) Tools/release/render-homebrew-formula.py \
		--product-version "$(DEVCONTAINER_VERSION)" \
		--formula-version "$$formula_version" \
		--formula-class DevcontainerCurrent \
		--url "https://github.com/stephenlclarke/devcontainer/releases/download/current/$$asset" \
		--conflicts-with devcontainer \
		--archive "$(DIST_DIR)/$$asset" \
		--template Tools/release/devcontainer.rb.in \
		--output "$(DIST_DIR)/devcontainer-current.rb"
	ruby -c "$(DIST_DIR)/devcontainer-current.rb"
release-check: check test-asan test-tsan parity-release homebrew-formula

release-gate-hosted: check homebrew-formula

docs:
	scripts/make-docs.sh "$(DOCS_OUTPUT_DIR)" "$(DOCS_HOSTING_BASE_PATH)"

serve-docs: docs
	$(PYTHON) -m http.server 8000 --directory "$(DOCS_OUTPUT_DIR)"

clean:
	$(PYTHON) Tools/ci/safe-clean.py
