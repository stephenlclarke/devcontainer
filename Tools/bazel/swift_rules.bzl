"""Project compile policy without overriding upstream package language modes."""

load("@build_bazel_rules_swift//swift:swift_binary.bzl", _swift_binary = "swift_binary")
load("@build_bazel_rules_swift//swift:swift_library.bzl", _swift_library = "swift_library")
load("@build_bazel_rules_swift//swift:swift_test.bzl", _swift_test = "swift_test")

_PROJECT_COPTS = ["-swift-version", "6", "-warnings-as-errors"]

def swift_binary(name, copts = [], **kwargs):
    _swift_binary(name = name, copts = _PROJECT_COPTS + copts, **kwargs)

def swift_library(name, copts = [], **kwargs):
    _swift_library(name = name, copts = _PROJECT_COPTS + copts, **kwargs)

def swift_test(name, copts = [], deps = [], **kwargs):
    _swift_test(name = name, copts = _PROJECT_COPTS + copts, deps = deps + ["//:DevContainerTestStorage"], **kwargs)
