// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0

import Darwin
import DevContainerProcess
import Foundation

// A separate process lets tests exercise real controlling-TTY ownership without
// changing descriptors or signal handlers in the concurrent Swift test host.
guard isatty(STDIN_FILENO) == 1 else { exit(90) }
alarm(10)
for _ in 0 ..< 12 {
    let status = try await ProcessRunner.inherited(
        executable: URL(fileURLWithPath: "/bin/sh"),
        arguments: ["-c", "read -r line; test \"$line\" = fixture || exit 91; printf 'tty-ok\\n'; exit 3"],
        environment: [:]
    )
    guard status == 3, tcgetpgrp(STDIN_FILENO) == getpgrp() else {
        print("tty-failure status=\(status) foreground=\(tcgetpgrp(STDIN_FILENO)) parent=\(getpgrp())")
        exit(92)
    }
}

alarm(0)
