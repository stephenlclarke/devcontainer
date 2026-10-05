// Copyright 2026 devcontainer project authors. SPDX-License-Identifier: Apache-2.0
package terminallauncher

import (
	"errors"
	"os/exec"
	"reflect"
	"syscall"
	"testing"
)

func TestParseArgumentsPreservesCommandVector(t *testing.T) {
	size, command, err := parseArguments([]string{"37", "113", "--", "/bin/sh", "-c", "printf '%s' '$HOME'"})
	if err != nil {
		t.Fatal(err)
	}
	if size != (WindowSize{Rows: 37, Cols: 113}) {
		t.Fatalf("unexpected size: %#v", size)
	}
	want := []string{"/bin/sh", "-c", "printf '%s' '$HOME'"}
	if !reflect.DeepEqual(command, want) {
		t.Fatalf("command = %#v, want %#v", command, want)
	}
}

func TestParseArgumentsRejectsMalformedInput(t *testing.T) {
	for _, args := range [][]string{
		{}, {"37", "113"}, {"37", "113", "--"}, {"37", "113", "x", "cmd"},
		{"65536", "113", "--", "cmd"},
		{"+37", "113", "--", "cmd"}, {"37", "1.5", "--", "cmd"},
	} {
		if _, _, err := parseArguments(args); err == nil {
			t.Errorf("parseArguments(%q) unexpectedly succeeded", args)
		}
	}
	if size, err := parseWindowSize("0"); err != nil || size != 0 {
		t.Fatalf("zero terminal dimension = %d, %v", size, err)
	}
}

func TestApplyWindowSizeSkipsUnconnectedDescriptorsAndStopsAtFirstTTY(t *testing.T) {
	var descriptors []uintptr
	err := applyWindowSize(WindowSize{Rows: 37, Cols: 113}, func(fd uintptr, _ WindowSize) error {
		descriptors = append(descriptors, fd)
		if fd < 2 {
			return syscall.ENOTTY
		}
		return nil
	})
	if err != nil || !reflect.DeepEqual(descriptors, []uintptr{0, 1, 2}) {
		t.Fatalf("error=%v descriptors=%v", err, descriptors)
	}
}

func TestApplyWindowSizeFailsWhenNoDescriptorIsATerminal(t *testing.T) {
	var descriptors []uintptr
	err := applyWindowSize(WindowSize{}, func(fd uintptr, _ WindowSize) error {
		descriptors = append(descriptors, fd)
		return syscall.EBADF
	})
	if !errors.Is(err, syscall.EBADF) || !reflect.DeepEqual(descriptors, []uintptr{0, 1, 2}) {
		t.Fatalf("error=%v descriptors=%v", err, descriptors)
	}
}

func TestApplyWindowSizeStopsOnUnexpectedIOCTLFailure(t *testing.T) {
	sentinel := errors.New("ioctl denied")
	var descriptors []uintptr
	err := applyWindowSize(WindowSize{}, func(fd uintptr, _ WindowSize) error {
		descriptors = append(descriptors, fd)
		return sentinel
	})
	if !errors.Is(err, sentinel) || !reflect.DeepEqual(descriptors, []uintptr{0}) {
		t.Fatalf("error=%v descriptors=%v", err, descriptors)
	}
}

func TestRunResizesThenExecsSameArgumentsAndEnvironment(t *testing.T) {
	args := []string{"37", "113", "--", "tool", "a b", "$HOME"}
	env := []string{"PATH=/test", "VALUE=$HOME"}
	var calls []string
	err := Run(args, env,
		func(size WindowSize) error {
			calls = append(calls, "resize")
			if size != (WindowSize{Rows: 37, Cols: 113}) {
				t.Fatalf("size = %#v", size)
			}
			return nil
		},
		func(command string) (string, error) {
			calls = append(calls, "lookup")
			if command != "tool" {
				t.Fatalf("lookup command = %q", command)
			}
			return "/resolved/tool", nil
		},
		func(path string, argv, actualEnv []string) error {
			calls = append(calls, "exec")
			if path != "/resolved/tool" || !reflect.DeepEqual(argv, args[3:]) || !reflect.DeepEqual(actualEnv, env) {
				t.Fatalf("exec received path=%q argv=%q env=%q", path, argv, actualEnv)
			}
			return errors.New("exec sentinel")
		})
	if err == nil || !reflect.DeepEqual(calls, []string{"resize", "lookup", "exec"}) {
		t.Fatalf("error=%v calls=%q", err, calls)
	}
}

func TestRunStopsAtFirstFailure(t *testing.T) {
	sentinel := errors.New("resize failed")
	lookedUp := false
	err := Run([]string{"37", "113", "--", "tool"}, nil,
		func(WindowSize) error { return sentinel },
		func(string) (string, error) { lookedUp = true; return "", nil },
		func(string, []string, []string) error { t.Fatal("exec called after resize failed"); return nil })
	if !errors.Is(err, sentinel) || lookedUp {
		t.Fatalf("error=%v lookedUp=%t", err, lookedUp)
	}
}

func TestRunLookupFailureDoesNotExec(t *testing.T) {
	sentinel := errors.New("not found")
	execed := false
	err := Run([]string{"37", "113", "--", "missing"}, nil,
		func(WindowSize) error { return nil },
		func(string) (string, error) { return "", sentinel },
		func(string, []string, []string) error { execed = true; return nil })
	if !errors.Is(err, sentinel) || execed {
		t.Fatalf("error=%v execed=%t", err, execed)
	}
}

func TestRunAcceptsLookPathErrDotAndExecsResolvedPath(t *testing.T) {
	gotPath := ""
	err := Run([]string{"0", "113", "--", "./tool", "arg"}, nil,
		func(WindowSize) error { return nil },
		func(string) (string, error) { return "./tool", exec.ErrDot },
		func(path string, argv, _ []string) error {
			gotPath = path
			if !reflect.DeepEqual(argv, []string{"./tool", "arg"}) {
				t.Fatalf("argv = %q", argv)
			}
			return errors.New("exec sentinel")
		})
	if gotPath != "./tool" || err == nil {
		t.Fatalf("path=%q error=%v", gotPath, err)
	}
}
