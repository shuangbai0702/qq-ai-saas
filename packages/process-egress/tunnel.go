package egress

import (
	"context"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"strings"

	"golang.zx2c4.com/wireguard/conn"
	"golang.zx2c4.com/wireguard/device"
	"golang.zx2c4.com/wireguard/tun/netstack"
)

// Config is local operator configuration. Endpoint is the WireGuard peer's
// numeric underlay address, not a destination supplied by a process.
type Config struct {
	Addresses  []netip.Addr
	Resolvers  []netip.Addr
	PrivateKey string // wg UAPI hex encoding, never logged
	PeerKey    string // wg UAPI hex encoding
	Endpoint   netip.AddrPort
}

// Tunnel owns one userspace network stack and one WireGuard peer. It does not
// install interfaces, DNS policy or routes in the host.
type Tunnel struct {
	dev   *device.Device
	stack *netstack.Net
	v4    bool
	v6    bool
	dns   bool
}

func Open(cfg Config) (*Tunnel, error) {
	if !validKey(cfg.PrivateKey) || !validKey(cfg.PeerKey) {
		return nil, errors.New("invalid WireGuard key")
	}
	if !validDestination(cfg.Endpoint) {
		return nil, errors.New("WireGuard endpoint requires a numeric address and nonzero port")
	}

	t := &Tunnel{dns: len(cfg.Resolvers) > 0}
	for _, addr := range cfg.Addresses {
		if !validAddress(addr) {
			return nil, errors.New("invalid tunnel address")
		}
		if addr.Is4() {
			if t.v4 {
				return nil, errors.New("only one tunnel IPv4 address is supported")
			}
			t.v4 = true
		} else {
			if t.v6 {
				return nil, errors.New("only one tunnel IPv6 address is supported")
			}
			t.v6 = true
		}
	}
	if !t.v4 && !t.v6 {
		return nil, errors.New("at least one tunnel address is required")
	}
	for _, addr := range cfg.Resolvers {
		if !t.supports(addr) {
			return nil, errors.New("resolver address family is unavailable inside the tunnel")
		}
	}

	var settings strings.Builder
	fmt.Fprintf(&settings, "private_key=%s\npublic_key=%s\nendpoint=%s\n",
		cfg.PrivateKey, cfg.PeerKey, cfg.Endpoint)
	if t.v4 {
		settings.WriteString("allowed_ip=0.0.0.0/0\n")
	}
	if t.v6 {
		settings.WriteString("allowed_ip=::/0\n")
	}

	stack, dev, err := openStack(cfg.Addresses, cfg.Resolvers, settings.String())
	if err != nil {
		return nil, err
	}
	t.stack, t.dev = stack, dev
	return t, nil
}

func (t *Tunnel) Close() {
	t.dev.Close()
}

func (t *Tunnel) DialTCP(ctx context.Context, target netip.AddrPort) (net.Conn, error) {
	if !validDestination(target) || !t.supports(target.Addr()) {
		return nil, errors.New("TCP destination is unavailable inside the tunnel")
	}
	c, err := t.stack.DialContextTCPAddrPort(ctx, target)
	if err != nil {
		return nil, err
	}
	return c, nil
}

func (t *Tunnel) DialUDP(target netip.AddrPort) (net.Conn, error) {
	if !validDestination(target) || !t.supports(target.Addr()) {
		return nil, errors.New("UDP destination is unavailable inside the tunnel")
	}
	c, err := t.stack.DialUDPAddrPort(netip.AddrPort{}, target)
	if err != nil {
		return nil, err
	}
	return c, nil
}

// Lookup sends an explicit query through this stack. It does not change or
// intercept the Windows DNS Client, including queries made by another process.
func (t *Tunnel) Lookup(ctx context.Context, name string) ([]string, error) {
	if !t.dns {
		return nil, errors.New("no resolver is configured for the tunnel")
	}
	return t.stack.LookupContextHost(ctx, strings.TrimSuffix(name, "."))
}

func openStack(addrs, resolvers []netip.Addr, settings string) (*netstack.Net, *device.Device, error) {
	tun, stack, err := netstack.CreateNetTUN(addrs, resolvers, 1280)
	if err != nil {
		return nil, nil, fmt.Errorf("create userspace network stack: %w", err)
	}
	dev := device.NewDevice(tun, conn.NewDefaultBind(), device.NewLogger(device.LogLevelSilent, ""))
	if err := dev.IpcSet(settings); err != nil {
		dev.Close()
		return nil, nil, errors.New("WireGuard rejected device settings")
	}
	if err := dev.Up(); err != nil {
		dev.Close()
		return nil, nil, fmt.Errorf("start WireGuard device: %w", err)
	}
	return stack, dev, nil
}

func (t *Tunnel) supports(addr netip.Addr) bool {
	return validAddress(addr) && ((addr.Is4() && t.v4) || (addr.Is6() && t.v6))
}

func validKey(s string) bool {
	b, err := hex.DecodeString(s)
	if err != nil || len(b) != 32 {
		return false
	}
	var nonzero byte
	for _, n := range b {
		nonzero |= n
	}
	return nonzero != 0
}

func validAddress(addr netip.Addr) bool {
	return addr.IsValid() && addr.Zone() == "" && !addr.Is4In6() &&
		!addr.IsUnspecified() && !addr.IsMulticast()
}

func validDestination(addr netip.AddrPort) bool {
	return addr.Port() != 0 && validAddress(addr.Addr())
}
