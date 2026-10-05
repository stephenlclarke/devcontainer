// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
//go:build linux

package terminallauncher

import (
	"runtime"
	"syscall"
	"unsafe"
)

const tiocswinsz = 0x5414

type linuxWinsize struct {
	rows uint16
	cols uint16
	x    uint16
	y    uint16
}

func ApplyTerminalSize(size WindowSize) error {
	return applyWindowSize(size, func(fd uintptr, requested WindowSize) error {
		value := linuxWinsize{rows: requested.Rows, cols: requested.Cols}
		_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, fd, tiocswinsz, uintptr(unsafe.Pointer(&value)))
		runtime.KeepAlive(&value)
		if errno == 0 {
			return nil
		}
		return errno
	})
}
