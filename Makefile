SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := workflow

DEVCONTAINER_VERSION ?= 1.0.1
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
SWIFT_COVERAGE_SCRATCH_PATH ?= .build/coverage
SWIFT_ASAN_SCRATCH_PATH ?= .build/asan
SWIFT_TSAN_SCRATCH_PATH ?= .build/tsan
SWIFT_TEST_RESULT_LOG ?= .build/swift-test.log
SWIFT_TEST_ATTEMPTS ?= 2
SWIFT_TEST_RUNNER_FLAGS ?= --no-parallel
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
.PHONY: parity-container-compose parity parity-vscode-docker
.PHONY: parity-vscode-apple-stock parity-vscode-container-compose parity-vscode
.PHONY: parity-release runtime-check
.PHONY: package package-release homebrew-formula homebrew-formula-current
.PHONY: release-version
.PHONY: prepare-release release-check release-gate-hosted sonar sonar-scan demo
.PHONY: clean
.PHONY: bazel-configure bazel-qualify bazel-test-tools bazel-build bazel-unit bazel-package bazel-acquire-releases bazel-cleanup bazel-checkpoint
.PHONY: bazel-coverage-report bazel-build-timings bazel-harness bazel-prepare-releases bazel-engine-case bazel-prepare-candidate
.PHONY: bazel-recover-runtime bazel-recover-runtime-apply
.PHONY: bazel-parity-report
.PHONY: bazel-prepare-guest-images
.PHONY: bazel-prepare-builders
.PHONY: bazel-prepare-guest-kernel
.PHONY: bazel-prepare-docker-oracle
.PHONY: bazel-prepare-docker-cli
.PHONY: bazel-prepare-devcontainers-cli
.PHONY: bazel-docs
export CASE_ID
export CASE_FIXTURE
BAZEL_PROFILE ?= enhanced
RELEASE_SET ?= Tools/bazel/releases.lock.json

# Opt-in native qualification; existing product/release entry points are unchanged.
bazel-configure:
	Tools/bazel/run.sh configure

bazel-qualify:
	Tools/bazel/run.sh coverage //:bazel_qualification

bazel-test-tools:
	Tools/bazel/run.sh test-tools

bazel-harness:
	Tools/bazel/run.sh test //Tools/testing:case_evidence_tests //Tools/bazel:release_preparation_tests

bazel-parity-report:
	@test -n "$(CAMPAIGN)" || { printf 'Set CAMPAIGN explicitly.\n' >&2; exit 2; }
	@Tools/bazel/run.sh parity-report "$(CAMPAIGN)" $(if $(CASE_FIXTURE),--fixture="$(CASE_FIXTURE)") --format="$${REPORT_FORMAT:-json}"

bazel-prepare-guest-images:
	Tools/bazel/run.sh prepare-guest-images Tools/bazel/guest-images.lock.json $(if $(filter 1,$(OFFLINE)),--offline)

bazel-prepare-builders:
	Tools/bazel/run.sh prepare-guest-images Tools/bazel/builder-images.lock.json $(if $(filter 1,$(OFFLINE)),--offline)

bazel-prepare-guest-kernel:
	Tools/bazel/run.sh prepare-releases Tools/bazel/guest-kernel.lock.json $(if $(filter 1,$(OFFLINE)),--offline)

bazel-prepare-docker-oracle:
	Tools/bazel/run.sh prepare-releases Tools/bazel/docker-oracle.lock.json $(if $(filter 1,$(OFFLINE)),--offline)

bazel-prepare-docker-cli:
	Tools/bazel/run.sh prepare-docker-cli Tools/bazel/docker-cli.lock.json $(if $(filter 1,$(OFFLINE)),--offline)

bazel-prepare-devcontainers-cli:
	Tools/bazel/run.sh prepare-devcontainers-cli Tools/bazel/devcontainers-cli.lock.json $(if $(filter 1,$(OFFLINE)),--offline)

bazel-engine-case:
	@test -n "$(CAMPAIGN)" -a -n "$(LANE)" || { printf 'Set CAMPAIGN and LANE explicitly.\n' >&2; exit 2; }
	Tools/bazel/run.sh test //Tools/testing:$(if $(filter docker,$(LANE)),released_docker_engine,released_engine_negotiation) --test_arg="--campaign=$(CAMPAIGN)" --test_arg="--lane=$(LANE)" --test_arg="--fixture=$${CASE_FIXTURE:-E01-engine-negotiation}" $(if $(CANDIDATE_INVOCATION),--test_arg="--candidate-invocation=$(CANDIDATE_INVOCATION)")

bazel-prepare-candidate:
	Tools/bazel/run.sh prepare-candidate "$(CANDIDATE_INVOCATION)"

bazel-recover-runtime:
	Tools/bazel/run.sh recover-runtime

bazel-recover-runtime-apply:
	Tools/bazel/run.sh recover-runtime --apply --case "$${CASE_ID}"

bazel-build:
	Tools/bazel/run.sh build --config=$(BAZEL_PROFILE) //:product

bazel-unit:
	Tools/bazel/run.sh coverage --config=$(BAZEL_PROFILE) //:unit

bazel-coverage-report:
	@test -n "$(INVOCATION)" || { printf 'Set INVOCATION to a retained source-unit coverage ID.\n' >&2; exit 2; }
	Tools/bazel/run.sh coverage-report "$(INVOCATION)"

bazel-build-timings:
	@test -n "$(INVOCATION)" || { printf 'Set INVOCATION to a measured build/test invocation ID.\n' >&2; exit 2; }
	@if [[ -n "$(BASELINE)" ]]; then \
		Tools/bazel/run.sh build-timings "$(INVOCATION)" --baseline "$(BASELINE)"; \
	else Tools/bazel/run.sh build-timings "$(INVOCATION)"; fi

bazel-package:
	Tools/bazel/run.sh test --config=$(BAZEL_PROFILE) --config=release //:candidate_archive //Tools/bazel:package_smoke

bazel-docs:
	Tools/bazel/run.sh test --config=$(BAZEL_PROFILE) --config=release //:documentation_tests

bazel-acquire-releases:
	Tools/bazel/run.sh acquire-releases "$(RELEASE_SET)"

bazel-prepare-releases:
	Tools/bazel/run.sh prepare-releases "$(RELEASE_SET)" $(if $(filter 1,$(OFFLINE)),--offline)

bazel-cleanup:
	Tools/bazel/run.sh cleanup

# Explicit coherent checkpoint, not the per-edit loop. Profile-specific actions
# are distinct; each invocation shares its native product/test dependency graph.
bazel-checkpoint:
	Tools/bazel/run.sh test-tools
	Tools/bazel/run.sh coverage --config=stock //:unit //:product
	Tools/bazel/run.sh test --config=stock --config=release //:candidate_archive //Tools/bazel:package_smoke
	Tools/bazel/run.sh coverage --config=enhanced //:unit //:product
	Tools/bazel/run.sh test --config=release //:candidate_archive //Tools/bazel:package_smoke
	Tools/bazel/run.sh cleanup --apply

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
	DEVCONTAINER_HOST_INTEGRATION=1 $(MAKE) swift-test

swift-test:
	@mkdir -p .build
	@$(SWIFT) build --quiet $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--build-tests
	@$(SWIFT) build --quiet $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--product devcontainer-engine
	@TEST_BIN_PATH="$$($(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
		--show-bin-path)"; \
		SWIFT_TEST_RESULT_LOG="$(SWIFT_TEST_RESULT_LOG)" \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		DEVCONTAINER_ENGINE_TEST_EXECUTABLE="$$TEST_BIN_PATH/devcontainer-engine" \
		Tools/ci/run-swift-test.sh \
		Tools/ci/run-swift-testing-bundle.sh \
		"$$TEST_BIN_PATH/devcontainerPackageTests.xctest/Contents/MacOS/devcontainerPackageTests" \
		$(SWIFT_TEST_RUNNER_FLAGS)

coverage:
	@worktree_changes="$$(git status --porcelain --untracked-files=all)"; \
	if [[ -n "$$worktree_changes" ]]; then \
		printf 'Coverage evidence requires a clean worktree:\n%s\n' \
			"$$worktree_changes" >&2; \
		exit 2; \
	fi
	@mkdir -p .build
	@find "$(SWIFT_COVERAGE_SCRATCH_PATH)" -type f \
		\( -name '*.profraw' -o -name '*.profdata' -o -name 'devcontainer.json' \) \
		-delete 2>/dev/null || true
	@PROFILE_SPOOL="$$(mktemp -d "$${TMPDIR:-/tmp}/devcontainer-swift-profile.XXXXXX")"; \
		trap 'rm -rf "$$PROFILE_SPOOL"' EXIT; \
		LLVM_PROFILE_FILE="$$PROFILE_SPOOL/swift-build-%m-%p.profraw" \
		$(SWIFT) build --quiet $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--scratch-path "$(SWIFT_COVERAGE_SCRATCH_PATH)" \
		--enable-code-coverage --build-tests; \
		$(SWIFT) build --quiet $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--scratch-path "$(SWIFT_COVERAGE_SCRATCH_PATH)" \
		--enable-code-coverage --product devcontainer-engine; \
		TEST_BIN_PATH="$$($(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
			--scratch-path "$(SWIFT_COVERAGE_SCRATCH_PATH)" \
			--show-bin-path)"; \
		mkdir -p "$$TEST_BIN_PATH/codecov"; \
		SWIFT_TEST_RESULT_LOG=.build/swift-coverage.log \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		SWIFT_TEST_ACCEPT_SIGNAL_13=0 \
		DEVCONTAINER_ENGINE_TEST_EXECUTABLE="$$TEST_BIN_PATH/devcontainer-engine" \
		LLVM_PROFILE_FILE="$$TEST_BIN_PATH/codecov/devcontainer-tests-%m-%p.profraw" \
		Tools/ci/run-swift-test.sh \
		Tools/ci/run-swift-testing-bundle.sh \
		"$$TEST_BIN_PATH/devcontainerPackageTests.xctest/Contents/MacOS/devcontainerPackageTests" \
		--enable-code-coverage $(SWIFT_TEST_RUNNER_FLAGS)
	@$(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
		$(SWIFT_STRICT_FLAGS) \
		--scratch-path "$(SWIFT_COVERAGE_SCRATCH_PATH)" \
		--enable-code-coverage \
		--product devcontainer
	@$(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
		$(SWIFT_STRICT_FLAGS) \
		--scratch-path "$(SWIFT_COVERAGE_SCRATCH_PATH)" \
		--enable-code-coverage \
		--product devcontainer-compose
	@Tools/coverage/run-cli-coverage.sh \
		"$$($(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
			--scratch-path "$(SWIFT_COVERAGE_SCRATCH_PATH)" \
			--show-bin-path)"
	@Tools/coverage/export-swift-coverage.sh \
		"$$($(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
			--scratch-path "$(SWIFT_COVERAGE_SCRATCH_PATH)" \
			--show-bin-path)" \
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
	@git rev-parse --verify HEAD > .build/sonar-coverage-revision

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
	worktree_changes="$$(git status --porcelain --untracked-files=all)"; \
	if [[ -n "$$worktree_changes" ]]; then \
		printf 'Sonar analysis requires a clean worktree:\n%s\n' \
			"$$worktree_changes" >&2; \
		exit 2; \
	fi; \
	head_version="$$(git rev-parse --verify HEAD)"; \
	coverage_version="$$(cat .build/sonar-coverage-revision 2>/dev/null || true)"; \
	if [[ "$$coverage_version" != "$$head_version" ]]; then \
		printf 'coverage.xml is not bound to checked-out HEAD %s; run make coverage-check\n' \
			"$$head_version" >&2; \
		exit 2; \
	fi; \
	sonar_project_version="$${SONAR_PROJECT_VERSION:-$$head_version}"; \
	if ! [[ "$$sonar_project_version" =~ ^[0-9a-f]{40}$$ ]]; then \
		printf 'SONAR_PROJECT_VERSION must be an exact lowercase commit SHA\n' >&2; \
		exit 2; \
	fi; \
	if [[ "$$sonar_project_version" != "$$head_version" ]]; then \
		printf 'SONAR_PROJECT_VERSION must match checked-out HEAD %s\n' \
			"$$head_version" >&2; \
		exit 2; \
	fi; \
	sonar_args=( \
		-Dsonar.projectVersion="$$sonar_project_version" \
		-Dsonar.qualitygate.wait="$(SONAR_QUALITYGATE_WAIT)" \
	); \
	if [[ -n "$${SONAR_PULL_REQUEST_KEY:-}" ]]; then \
		if ! [[ "$${SONAR_PULL_REQUEST_KEY}" =~ ^[0-9]+$$ ]] \
			|| [[ -z "$${SONAR_PULL_REQUEST_BRANCH:-}" ]] \
			|| [[ -z "$${SONAR_PULL_REQUEST_BASE:-}" ]]; then \
			printf 'complete Sonar pull-request identity is required\n' >&2; \
			exit 2; \
		fi; \
		sonar_args+=( \
			-Dsonar.pullrequest.key="$${SONAR_PULL_REQUEST_KEY}" \
			-Dsonar.pullrequest.branch="$${SONAR_PULL_REQUEST_BRANCH}" \
			-Dsonar.pullrequest.base="$${SONAR_PULL_REQUEST_BASE}" \
		); \
	fi; \
	attempt=1; \
	while true; do \
		set +e; \
		SONAR_TOKEN="$$sonar_token" sonar-scanner "$${sonar_args[@]}"; \
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
	@$(SWIFT) build --quiet $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--scratch-path "$(SWIFT_ASAN_SCRATCH_PATH)" \
		--sanitize=address --build-tests
	@$(SWIFT) build --quiet $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--scratch-path "$(SWIFT_ASAN_SCRATCH_PATH)" \
		--sanitize=address --product devcontainer-engine
	@TEST_BIN_PATH="$$($(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
		--scratch-path "$(SWIFT_ASAN_SCRATCH_PATH)" \
		--show-bin-path)"; \
		SWIFT_TEST_RESULT_LOG=.build/swift-asan.log \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		SWIFT_TEST_ACCEPT_SIGNAL_13=0 \
		DEVCONTAINER_ENGINE_TEST_EXECUTABLE="$$TEST_BIN_PATH/devcontainer-engine" \
		Tools/ci/run-swift-test.sh \
		Tools/ci/run-swift-testing-bundle.sh \
		"$$TEST_BIN_PATH/devcontainerPackageTests.xctest/Contents/MacOS/devcontainerPackageTests" \
		--sanitize=address $(SWIFT_TEST_RUNNER_FLAGS)

tsan:
	@$(SWIFT) build --quiet $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--scratch-path "$(SWIFT_TSAN_SCRATCH_PATH)" \
		--sanitize=thread --build-tests
	@$(SWIFT) build --quiet $(SWIFT_RESOLVED_FLAGS) $(SWIFT_STRICT_FLAGS) \
		--scratch-path "$(SWIFT_TSAN_SCRATCH_PATH)" \
		--sanitize=thread --product devcontainer-engine
	@TEST_BIN_PATH="$$($(SWIFT) build $(SWIFT_RESOLVED_FLAGS) \
		--scratch-path "$(SWIFT_TSAN_SCRATCH_PATH)" \
		--show-bin-path)"; \
		SWIFT_TEST_RESULT_LOG=.build/swift-tsan.log \
		SWIFT_TEST_ATTEMPTS="$(SWIFT_TEST_ATTEMPTS)" \
		SWIFT_TEST_ACCEPT_SIGNAL_13=0 \
		DEVCONTAINER_ENGINE_TEST_EXECUTABLE="$$TEST_BIN_PATH/devcontainer-engine" \
		Tools/ci/run-swift-test.sh \
		Tools/ci/run-swift-testing-bundle.sh \
		"$$TEST_BIN_PATH/devcontainerPackageTests.xctest/Contents/MacOS/devcontainerPackageTests" \
		--sanitize=thread $(SWIFT_TEST_RUNNER_FLAGS)

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
	$(SWIFTLINT) lint --strict --quiet \
		Sources Tests Plugins Tools/version-generator
	$(SWIFTFORMAT) Sources Tests Plugins Tools/version-generator --lint
	bash -n Tools/ci/*.sh Tools/coverage/*.sh Tools/parity/*.sh \
		Tools/release/*.sh scripts/*.sh
	shellcheck Tools/ci/*.sh Tools/coverage/*.sh Tools/parity/*.sh \
		Tools/release/*.sh scripts/*.sh
	$(ACTIONLINT)

format:
	$(SWIFTFORMAT) Sources Tests Plugins Tools/version-generator

format-check:
	$(SWIFTFORMAT) Sources Tests Plugins Tools/version-generator --lint

parity-manifest:
	$(PYTHON) Tools/parity/validate_manifest.py
	$(PYTHON) Tools/parity/validate_coverage.py
	$(PYTHON) Tools/parity/validate_fixture_pins.py

parity-docker:
	Tools/parity/run-lane.sh docker "$(PARITY_EVIDENCE_DIR)"

parity-apple-stock:
	Tools/parity/run-lane.sh apple-stock "$(PARITY_EVIDENCE_DIR)"

parity-container-compose:
	Tools/parity/run-lane.sh container-compose "$(PARITY_EVIDENCE_DIR)"

parity: parity-docker parity-apple-stock parity-container-compose
	$(PYTHON) Tools/parity/compare_results.py "$(PARITY_EVIDENCE_DIR)"

parity-vscode:
	Tools/parity/run-vscode.sh "$(PARITY_EVIDENCE_DIR)"

parity-vscode-docker:
	Tools/parity/run-vscode.sh "$(PARITY_EVIDENCE_DIR)" docker

parity-vscode-apple-stock:
	Tools/parity/run-vscode.sh "$(PARITY_EVIDENCE_DIR)" apple-stock

parity-vscode-container-compose:
	Tools/parity/run-vscode.sh "$(PARITY_EVIDENCE_DIR)" container-compose

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

demo:
	Tools/release/record-vhs-live-demo.sh \
		docs/devcontainer-demo.tape \
		docs/images/devcontainer-demo.gif

clean:
	$(PYTHON) Tools/ci/safe-clean.py
