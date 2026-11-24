package egress

import (
	"bufio"
	"context"
	"io"
	"os/exec"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"golang.org/x/sys/windows"
)

func TestPinSameExecutableInDifferentJobs(t *testing.T) {
	exe, err := exec.LookPath("node.exe")
	if err != nil {
		t.Skip("Windows identity fixture requires Node")
	}
	jobA, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer windows.CloseHandle(jobA)
	jobB, err := windows.CreateJobObject(nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer windows.CloseHandle(jobB)

	parentA, childA, withJobA := startNode(t, exe, jobA)
	parentB, childB, withJobB := startNode(t, exe, jobB)
	direct, _, _ := startNode(t, exe, 0)
	if !strings.EqualFold(parentA.Image, parentB.Image) || !strings.EqualFold(parentA.Image, direct.Image) {
		t.Fatal("test processes did not start the same executable")
	}

	for _, pair := range []struct {
		ref ProcessRef
		job windows.Handle
	}{{parentA, jobA}, {childA, jobA}, {parentB, jobB}, {childB, jobB}} {
		pinned, err := PinJobProcess(pair.ref, pair.job)
		if err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { pinned.Close() })
		if err := pinned.CheckAlive(); err != nil {
			t.Fatal(err)
		}
	}
	stale := parentA
	stale.CreatedFileTime++
	for _, ref := range []ProcessRef{stale, parentB, childB, direct} {
		if pinned, err := PinJobProcess(ref, jobA); err == nil {
			pinned.Close()
			t.Fatal("stale identity or nonmember matched job A")
		}
	}

	pinned, err := PinJobProcess(parentA, jobA)
	if err != nil {
		t.Fatal(err)
	}
	defer pinned.Close()
	withJobA()
	if err := pinned.CheckAlive(); err == nil {
		t.Fatal("exited process still appears live")
	}
	if pinned, err := PinJobProcess(parentB, jobB); err != nil {
		t.Fatal(err)
	} else {
		pinned.Close()
	}
	withJobB()
}

func startNode(t *testing.T, exe string, job windows.Handle) (parent, child ProcessRef, stop func()) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	source := "process.stdin.once('data',()=>{" +
		"const c=require('node:child_process').spawn(process.execPath," +
		"['-e','process.stdin.resume()'],{stdio:['pipe','ignore','ignore']});" +
		"console.log(c.pid);}); process.stdin.resume();"
	cmd := exec.CommandContext(ctx, exe, "-e", source)
	in, err := cmd.StdinPipe()
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	out, err := cmd.StdoutPipe()
	if err != nil {
		cancel()
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		cancel()
		t.Fatal(err)
	}
	stop = sync.OnceFunc(func() {
		in.Close()
		cancel()
		cmd.Wait()
	})
	t.Cleanup(stop)

	pid := uint32(cmd.Process.Pid)
	handle, err := windows.OpenProcess(windows.PROCESS_SET_QUOTA|windows.PROCESS_TERMINATE|
		windows.PROCESS_QUERY_LIMITED_INFORMATION, false, pid)
	if err != nil {
		t.Fatal(err)
	}
	defer windows.CloseHandle(handle)
	if job != 0 {
		if err := windows.AssignProcessToJobObject(job, handle); err != nil {
			t.Fatal(err)
		}
	}
	parent, err = processRef(handle, pid)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := io.WriteString(in, "\n"); err != nil {
		t.Fatal(err)
	}
	line, err := bufio.NewReader(io.LimitReader(out, 64)).ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	childPID, err := strconv.ParseUint(strings.TrimSpace(line), 10, 32)
	if err != nil {
		t.Fatal("test child PID unavailable:", err)
	}
	childHandle, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION, false, uint32(childPID))
	if err != nil {
		t.Fatal(err)
	}
	defer windows.CloseHandle(childHandle)
	child, err = processRef(childHandle, uint32(childPID))
	if err != nil {
		t.Fatal(err)
	}
	return parent, child, stop
}
