"""Import one pinned package graph, selecting the stock or enhanced lockfile.

Package manifests remain owned by SwiftPM; rules_swift_package_manager generates
their native Bazel targets. This extension only selects immutable repository pins.
"""

load("@rules_swift_package_manager//swiftpkg:defs.bzl", "swift_package")
load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_archive")

_GROUP_PACKAGES = {
    "containerization": "containerization",
    "container-engine-api": "engine-api",
    "container": "container-sdk",
}

def _group(identity):
    if identity in _GROUP_PACKAGES:
        return _GROUP_PACKAGES[identity]
    if identity in ["swift-argument-parser", "swift-docc-plugin", "swift-docc-symbolkit"]:
        return None
    return "foundation"

def _dependencies_impl(ctx):
    profile = ctx.getenv("DEVCONTAINER_RUNTIME_PROFILE", "enhanced")
    modes = {
        "foundation": (ctx.getenv("DEVCONTAINER_FOUNDATION_LAYER", "source"), ctx.getenv("DEVCONTAINER_FOUNDATION_LAYER_MIRROR", "")),
        "containerization": (ctx.getenv("DEVCONTAINER_CONTAINERIZATION_LAYER", "source"), ctx.getenv("DEVCONTAINER_CONTAINERIZATION_LAYER_MIRROR", "")),
        "engine-api": (ctx.getenv("DEVCONTAINER_ENGINE_API_LAYER", "source"), ctx.getenv("DEVCONTAINER_ENGINE_API_LAYER_MIRROR", "")),
        "container-sdk": (ctx.getenv("DEVCONTAINER_CONTAINER_SDK_LAYER", "source"), ctx.getenv("DEVCONTAINER_CONTAINER_SDK_LAYER_MIRROR", "")),
    }
    if profile not in ["stock", "enhanced"]:
        fail("DEVCONTAINER_RUNTIME_PROFILE must be stock or enhanced")
    for group, selection in modes.items():
        if selection[0] not in ["source", "prebuilt"] or (selection[1] and selection[0] != "prebuilt"):
            fail(group + " mode and mirror must be selected together")
        if selection[0] == "prebuilt" and not selection[1]:
            fail(group + " binary requires a verified local mirror from the launcher")
    if modes["foundation"][0] == "prebuilt" and ctx.getenv("DEVCONTAINER_ARGUMENT_PARSER_LAYER", "source") != "prebuilt":
        fail("foundation requires published ArgumentParser")
    for group in ["containerization", "engine-api"]:
        if modes[group][0] == "prebuilt" and modes["foundation"][0] != "prebuilt":
            fail(group + " binary requires the published foundation")
    if modes["container-sdk"][0] == "prebuilt" and (modes["containerization"][0] != "prebuilt" or modes["engine-api"][0] != "prebuilt"):
        fail("ContainerSDK binary requires both published upper lower groups")
    for mod in ctx.modules:
        for config in mod.tags.lockfiles:
            selected = config.stock if profile == "stock" else config.enhanced
            pins = json.decode(ctx.read(selected))["pins"]
            names = []
            for pin in pins:
                if pin["kind"] != "remoteSourceControl":
                    fail("Only immutable source-control package pins are supported")
                revision = pin["state"]["revision"]
                if len(revision) != 40 or any([c not in "0123456789abcdef" for c in revision.elems()]):
                    fail("Package revision must be a lowercase 40-character commit SHA")
                name = "swiftpkg_" + pin["identity"].replace("-", "_").replace(".", "_")
                if name in names:
                    fail("Duplicate package repository: " + name)
                names.append(name)
                group = _group(pin["identity"])
                if group != None and modes[group][0] == "prebuilt":
                    lock = json.decode(ctx.read(Label("//Tools/bazel/artifacts:" + group + "-" + profile + ".lock.json")))
                    if (lock.get("schema") != 1 or lock.get("group") != group or lock.get("profile") != profile or
                        lock.get("developmentProof") != False or lock.get("configuredRoot") != "//:products" or
                        lock.get("sourcePins", {}).get(pin["identity"]) != revision or
                        len(lock.get("archiveSHA256", "")) != 64):
                        fail(group + " bundle does not match the selected profile pin")
                    mirror = modes[group][1]
                    if not mirror.startswith("file:///") or " " in mirror or "#" in mirror or ".." in mirror.split("/"):
                        fail(group + " mirror must be an encoded absolute file URL")
                    http_archive(
                        name = name,
                        urls = [mirror],
                        sha256 = lock["archiveSHA256"],
                        strip_prefix = group + "/" + name,
                    )
                    continue
                patches = []
                if profile == "enhanced" and pin["identity"] == "containerization":
                    patches = ["//Tools/bazel:containerization-ext4-unaligned.patch"]
                if profile == "enhanced" and pin["identity"] == "container-engine-api":
                    patches = ["//Tools/bazel:gateway-recovery-capability.patch"]
                if profile == "enhanced" and pin["identity"] == "zstd":
                    patches = ["//Tools/bazel:zstd-public-module.patch"]
                swift_package(
                    name = name,
                    bazel_package_name = name,
                    remote = pin["location"],
                    commit = revision,
                    version = pin["state"].get("version", ""),
                    publicly_expose_all_targets = True,
                    patches = patches,
                    patch_args = ["-p1"],
                )
    # The watched lockfile and profile fully determine immutable rule attributes.
    # Do not write a different generated-repository snapshot into the tracked
    # module lock every time the stock/enhanced profile changes.
    return ctx.extension_metadata(reproducible = True)

dependencies = module_extension(
    implementation = _dependencies_impl,
    tag_classes = {
        "lockfiles": tag_class(attrs = {
            "stock": attr.label(mandatory = True),
            "enhanced": attr.label(mandatory = True),
        }),
    },
)
