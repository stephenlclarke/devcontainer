"""Bounded direct-HTTP E04 execution with durable image ownership and timing."""

import time

from build_images import BuildImages
from build_probe import MAX_BUILD_OUTPUT, build_context, build_route, require_failed_run, tag_for
from engine_probe import request
from guest_fixture import GuestFixture


class BuildFixture(GuestFixture):
    """No container creation here; builder admission belongs to the runtime owner."""

    def __init__(self, socket, owner, image, api_version, journal, base, *, observe=None, before_submit=None,
                 private_named_cleanup=False):
        super().__init__(socket, owner, image, api_version, journal, observe=observe)
        self.base = base
        self.before_submit = before_submit
        self.images = BuildImages(self, journal, owner, base, private_named_cleanup=private_named_cleanup)

    def submit(self, *, failing=False):
        body = build_context(self.base, self.owner, failing=failing)
        event = {"method": "POST", "route": f"/v{self.version}" + build_route(self.owner, failing=failing)}
        if self.before_submit is not None:
            self.before_submit()
        self.images.start(failing=failing)
        started = time.monotonic_ns()
        try:
            status, payload = request(self.socket, "POST", event["route"], body, timeout=180,
                                      content_type="application/x-tar", max_bytes=MAX_BUILD_OUTPUT)
            event["status"] = status
            result = self.images.response(status, payload, failing=failing)
            if failing:
                require_failed_run(payload, self.owner)
            elif result.failed or not result.progress:
                raise ValueError("Successful build did not return successful progress")
            return result
        except (Exception, KeyboardInterrupt) as error:
            event["error"] = type(error).__name__
            raise
        finally:
            event["durationNS"] = time.monotonic_ns() - started
            if self.observe is not None:
                self.observe(event)

    def operation(self):
        self.images.prepare()
        success = self.submit()
        actual = self.images.inspect(tag_for(self.owner))
        if actual is None or not self.images.identity(actual, False)["inspect_label"]:
            raise ValueError("Successful build output lacks the expected identity and label")
        self.submit(failing=True)
        if self.images.inspect(tag_for(self.owner, failing=True)) is not None:
            raise ValueError("Failed build unexpectedly published its output tag")
        return {"build_progress": str(success.progress).lower(), "failed_build": "true", "inspect_label": "true"}

    def cleanup(self):
        if "e04-images-intent.json" in self.journal.records():
            self.images.cleanup()
        return {"status": "passed", "remainingOwnedResources": []}
