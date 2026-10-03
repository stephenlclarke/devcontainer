// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import ContainerResource
@testable import DevContainerAppleRuntime

actor FakeManagedNetworkAllocations: AppleNetworkAllocationClient {
    let attachment: ContainerResource.Attachment?
    var failOnce: Bool

    init(attachment: ContainerResource.Attachment?, failOnce: Bool = false) {
        self.attachment = attachment
        self.failOnce = failOnce
    }

    func lookup(network _: String, plugin _: String, hostname _: String) -> ContainerResource.Attachment? {
        if failOnce {
            failOnce = false
            return nil
        }
        return attachment
    }
}
