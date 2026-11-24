package egress

import (
	"errors"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
	"unsafe"

	"golang.org/x/sys/windows"
)

var isProcessInJob = windows.NewLazySystemDLL("kernel32.dll").NewProc("IsProcessInJob")

type ProcessRef struct {
	PID             uint32 `json:"pid"`
	CreatedFileTime uint64 `json:"createdFileTime,string"`
	Image           string `json:"image"`
}

// PinnedProcess retains a Windows process object across PID reuse. This is
// an identity check. The caller needs a separate kernel network policy to
// authorize a flow or prevent a process from sending traffic.
type PinnedProcess struct {
	mu     sync.Mutex
	handle windows.Handle
	ref    ProcessRef
}

// PinJobProcess expects a job handle from the trusted local launcher and
// the exact process identity. Being in any job is insufficient.
func PinJobProcess(ref ProcessRef, trustedJob windows.Handle) (*PinnedProcess, error) {
	if ref.PID == 0 || ref.CreatedFileTime == 0 || !filepath.IsAbs(ref.Image) ||
		trustedJob == 0 || trustedJob == windows.InvalidHandle {
		return nil, errors.New("incomplete process or job identity")
	}
	h, err := windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION|windows.SYNCHRONIZE, false, ref.PID)
	if err != nil {
		return nil, fmt.Errorf("open process: %w", err)
	}
	matched := false
	defer func() {
		if !matched {
			windows.CloseHandle(h)
		}
	}()

	actual, err := processRef(h, ref.PID)
	if err != nil {
		return nil, err
	}
	if actual.CreatedFileTime != ref.CreatedFileTime || !strings.EqualFold(filepath.Clean(actual.Image), filepath.Clean(ref.Image)) {
		return nil, errors.New("process creation time or image does not match")
	}
	var member int32
	ok, _, callErr := isProcessInJob.Call(uintptr(h), uintptr(trustedJob), uintptr(unsafe.Pointer(&member)))
	if ok == 0 {
		return nil, fmt.Errorf("verify job membership: %w", callErr)
	}
	if member == 0 {
		return nil, errors.New("process does not belong to the launcher's job")
	}
	if err := ensureRunning(h); err != nil {
		return nil, err
	}
	matched = true
	return &PinnedProcess{handle: h, ref: actual}, nil
}

func (p *PinnedProcess) Ref() ProcessRef {
	return p.ref
}

func (p *PinnedProcess) CheckAlive() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.handle == 0 {
		return errors.New("process handle is closed")
	}
	return ensureRunning(p.handle)
}

func (p *PinnedProcess) Close() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.handle == 0 {
		return nil
	}
	h := p.handle
	p.handle = 0
	return windows.CloseHandle(h)
}

func processRef(h windows.Handle, pid uint32) (ProcessRef, error) {
	var creation, exit, kernel, user windows.Filetime
	if err := windows.GetProcessTimes(h, &creation, &exit, &kernel, &user); err != nil {
		return ProcessRef{}, fmt.Errorf("get process creation time: %w", err)
	}
	path := make([]uint16, 32768)
	pathLen := uint32(len(path))
	if err := windows.QueryFullProcessImageName(h, 0, &path[0], &pathLen); err != nil {
		return ProcessRef{}, fmt.Errorf("get process image: %w", err)
	}
	return ProcessRef{
		PID:             pid,
		CreatedFileTime: uint64(creation.HighDateTime)<<32 | uint64(creation.LowDateTime),
		Image:           windows.UTF16ToString(path[:pathLen]),
	}, nil
}

func ensureRunning(h windows.Handle) error {
	result, err := windows.WaitForSingleObject(h, 0)
	if err != nil {
		return fmt.Errorf("query process lifetime: %w", err)
	}
	if result != uint32(windows.WAIT_TIMEOUT) {
		return errors.New("process has already exited")
	}
	return nil
}
