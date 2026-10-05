// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
package terminallauncher

import (
	"errors"
	"fmt"
	"os/exec"
	"strconv"
	"syscall"
)

type WindowSize struct {
	Rows uint16
	Cols uint16
}

func parseWindowSize(value string) (uint16, error) {
	if value == "" {
		return 0, errors.New("terminal dimensions must be decimal integers")
	}
	for _, char := range value {
		if char < '0' || char > '9' {
			return 0, errors.New("terminal dimensions must be decimal integers")
		}
	}
	parsed, err := strconv.ParseUint(value, 10, 16)
	if err != nil {
		return 0, errors.New("terminal dimensions must be between 0 and 65535")
	}
	return uint16(parsed), nil
}

func parseArguments(args []string) (WindowSize, []string, error) {
	if len(args) < 4 || args[2] != "--" || len(args) == 3 {
		return WindowSize{}, nil, errors.New("usage: devcontainer-terminal-linux ROWS COLS -- COMMAND [ARG ...]")
	}
	rows, err := parseWindowSize(args[0])
	if err != nil {
		return WindowSize{}, nil, fmt.Errorf("invalid rows: %w", err)
	}
	cols, err := parseWindowSize(args[1])
	if err != nil {
		return WindowSize{}, nil, fmt.Errorf("invalid columns: %w", err)
	}
	return WindowSize{Rows: rows, Cols: cols}, args[3:], nil
}

type ResizeTerminal func(WindowSize) error
type LookPath func(string) (string, error)
type ExecProcess func(string, []string, []string) error

func applyWindowSize(size WindowSize, ioctl func(uintptr, WindowSize) error) error {
	var lastErr error
	for fd := uintptr(0); fd <= 2; fd++ {
		err := ioctl(fd, size)
		if err == nil {
			return nil
		}
		if errors.Is(err, syscall.EBADF) || errors.Is(err, syscall.ENOTTY) {
			lastErr = err
			continue
		}
		return err
	}
	return lastErr
}

func Run(args, environment []string, resize ResizeTerminal, find LookPath, replace ExecProcess) error {
	size, command, err := parseArguments(args)
	if err != nil {
		return err
	}
	if err := resize(size); err != nil {
		return fmt.Errorf("set terminal size: %w", err)
	}
	path, err := find(command[0])
	if err != nil && !errors.Is(err, exec.ErrDot) {
		return fmt.Errorf("find command %q: %w", command[0], err)
	}
	if err := replace(path, command, environment); err != nil {
		return fmt.Errorf("exec command %q: %w", command[0], err)
	}
	return errors.New("exec returned without replacing the process")
}
