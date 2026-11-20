// wgprobe checks one configured tunnel. It is a one-shot operator tool with
// no forwarding listener and no process admission authority.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"strings"
	"time"

	egress "fishbot.local/process-egress"
)

func main() {
	target := flag.String("target", "", "numeric HTTPS test endpoint (IP:port)")
	name := flag.String("server-name", "", "HTTPS test host name for TLS verification")
	expected := flag.String("expect-ip", "", "expected observed public IP")
	dns := flag.String("dns", "", "optional domain to resolve inside the tunnel")
	flag.Parse()

	if err := probe(*target, *name, *expected, *dns); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func probe(rawTarget, name, expected, dns string) error {
	target, err := netip.ParseAddrPort(rawTarget)
	if err != nil || target.Port() == 0 {
		return errors.New("-target must be a numeric IP and port")
	}
	want, err := netip.ParseAddr(expected)
	if err != nil || want.Zone() != "" {
		return errors.New("-expect-ip must be the expected observed IP")
	}
	host := &url.URL{Scheme: "https", Host: name, Path: "/"}
	if name == "" || strings.ContainsAny(name, ":%/?#@[]\\ \t\r\n") {
		return errors.New("-server-name must be a TLS host name")
	}

	input := json.NewDecoder(io.LimitReader(os.Stdin, 16*1024))
	input.DisallowUnknownFields()
	var cfg egress.Config
	if err := input.Decode(&cfg); err != nil {
		return errors.New("invalid tunnel configuration on stdin")
	}
	if err := input.Decode(new(json.RawMessage)); err != io.EOF {
		return errors.New("unexpected data after tunnel configuration")
	}

	path, err := egress.Open(cfg)
	if err != nil {
		return err
	}
	defer path.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()

	transport := &http.Transport{
		DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			if network != "tcp" {
				return nil, errors.New("test endpoint requires TCP")
			}
			return path.DialTCP(ctx, target)
		},
		DisableKeepAlives: true,
	}
	defer transport.CloseIdleConnections()
	client := &http.Client{
		Transport: transport,
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, host.String(), nil)
	if err != nil {
		return errors.New("invalid HTTPS probe URL")
	}
	start := time.Now()
	response, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("tunnel HTTPS probe: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("unexpected HTTPS probe status: %d", response.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(response.Body, 128))
	if err != nil {
		return fmt.Errorf("read HTTPS probe reply: %w", err)
	}
	got, err := netip.ParseAddr(strings.TrimSpace(string(body)))
	if err != nil || got != want {
		return errors.New("test endpoint did not return the expected observed source IP")
	}
	result := struct {
		SourceIP  netip.Addr `json:"sourceIp"`
		ElapsedMS int64      `json:"httpsMilliseconds"`
		DNS       []string   `json:"dns,omitempty"`
	}{SourceIP: got, ElapsedMS: time.Since(start).Milliseconds()}
	if dns != "" {
		result.DNS, err = path.Lookup(ctx, dns)
		if err != nil {
			return fmt.Errorf("tunnel DNS probe: %w", err)
		}
	}
	return json.NewEncoder(os.Stdout).Encode(result)
}
