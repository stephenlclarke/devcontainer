#!/usr/bin/env bash
set -euo pipefail

required=(swift python3 ruby make git)
optional=(markdownlint swiftlint swiftformat shellcheck jq vhs)

for tool in "${required[@]}"; do
  command -v "$tool" >/dev/null || {
    printf 'required tool is missing: %s\n' "$tool" >&2
    exit 1
  }
done

minimum_ruby_version="2.7"
ruby -e '
begin
  require "json"
  require "yaml"
rescue LoadError => error
  abort("Ruby JSON and YAML/Psych are required: #{error.message}")
end
minimum = ARGV.fetch(0).split(".").map(&:to_i)
actual = RUBY_VERSION.split(".").map(&:to_i)
abort("Ruby #{ARGV.fetch(0)} or newer is required; found #{RUBY_VERSION}") if (actual <=> minimum) == -1
probe = YAML.safe_load("---\nprobe: true\n")
abort("Ruby YAML/Psych probe failed") unless probe == {"probe" => true}
probe = JSON.parse(JSON.generate({"probe" => true}))
abort("Ruby JSON probe failed") unless probe == {"probe" => true}
' "${minimum_ruby_version}"

for tool in "${optional[@]}"; do
  command -v "$tool" >/dev/null || printf 'optional quality tool is missing: %s\n' "$tool" >&2
done

swift --version
python3 --version
ruby --version
git --version
