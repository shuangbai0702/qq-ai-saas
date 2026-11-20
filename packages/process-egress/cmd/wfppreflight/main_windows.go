// wfppreflight opens a read-only WFP session to check local API access.
// Successful access is not evidence of process interception or leak protection.
package main

import (
	"encoding/json"
	"os"
	"unsafe"

	"golang.org/x/sys/windows"
)

func main() {
	api := windows.NewLazySystemDLL("fwpuclnt.dll")
	open := api.NewProc("FwpmEngineOpen0")
	closeEngine := api.NewProc("FwpmEngineClose0")
	if err := open.Find(); err != nil {
		fail("WFP API unavailable")
	}
	if err := closeEngine.Find(); err != nil {
		fail("WFP close API unavailable")
	}
	var handle windows.Handle
	// RPC_C_AUTHN_WINNT = 10. A null session uses a nondynamic session;
	// this command never adds, removes or changes any WFP object.
	code, _, _ := open.Call(0, 10, 0, 0, uintptr(unsafe.Pointer(&handle)))
	if code != 0 {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"wfpAccessible": false, "win32Error": uint32(code)})
		os.Exit(1)
	}
	closeCode, _, _ := closeEngine.Call(uintptr(handle))
	json.NewEncoder(os.Stdout).Encode(map[string]any{
		"wfpAccessible":                 true,
		"sessionClosed":                 closeCode == 0,
		"napcatInterceptionImplemented": false,
		"failClosedValidated":           false,
	})
	if closeCode != 0 {
		os.Exit(1)
	}
}

func fail(message string) {
	json.NewEncoder(os.Stdout).Encode(map[string]any{"wfpAccessible": false, "error": message})
	os.Exit(1)
}
