// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
package main

import (
	"fmt"
	"os"
	"os/exec"
	"syscall"

	"github.com/stephenlclarke/devcontainer/Tools/terminal-launcher"
)

func main() {
	err := terminallauncher.Run(os.Args[1:], os.Environ(), terminallauncher.ApplyTerminalSize, exec.LookPath, syscall.Exec)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
