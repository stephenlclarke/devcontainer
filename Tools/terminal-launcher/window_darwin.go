// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
//go:build darwin

package terminallauncher

// The shipped binary targets Linux only. This host implementation keeps the
// injected launcher tests buildable on macOS without changing a host terminal.
func ApplyTerminalSize(WindowSize) error { return nil }
