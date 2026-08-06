//go:build linux

package app

import (
	"os"

	"golang.org/x/sys/unix"
)

// isInteractiveTTY reports whether stdin and stdout are real interactive terminals.
// FileMode.ModeCharDevice is unreliable for some redirected/special FDs (agent
// runners can report char-device bits on non-terminals), so use termios ioctl.
func isInteractiveTTY() bool {
	return isTerminalFD(int(os.Stdin.Fd())) && isTerminalFD(int(os.Stdout.Fd()))
}

func isTerminalFD(fd int) bool {
	if fd < 0 {
		return false
	}
	_, err := unix.IoctlGetTermios(fd, unix.TCGETS)
	return err == nil
}
