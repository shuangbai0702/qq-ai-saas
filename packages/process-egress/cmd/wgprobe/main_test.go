package main

import (
	"strings"
	"testing"
)

func TestReadConfigLimits(t *testing.T) {
	for _, data := range []string{
		"{}" + strings.Repeat(" ", 16*1024),
		"{} {}",
		"{\"unknown\":true}",
		"{\"privateKey\":\"secret-value\",",
	} {
		if _, err := readConfig(strings.NewReader(data)); err == nil {
			t.Fatal("invalid configuration accepted")
		} else if strings.Contains(err.Error(), "secret-value") {
			t.Fatal("secret echoed")
		}
	}
	if _, err := readConfig(strings.NewReader("{}")); err != nil {
		t.Fatal(err)
	}
}

func TestProbeIPRejectsTruncatedReply(t *testing.T) {
	if _, err := readProbeIP(strings.NewReader("192.0.2.1" + strings.Repeat(" ", 128) + "unexpected")); err == nil {
		t.Fatal("oversized response accepted")
	}
	if ip, err := readProbeIP(strings.NewReader("192.0.2.1\n")); err != nil || ip.String() != "192.0.2.1" {
		t.Fatal(ip, err)
	}
}
