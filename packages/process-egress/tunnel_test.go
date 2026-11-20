package egress

import (
	"context"
	"crypto/ecdh"
	"crypto/rand"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"io"
	"net"
	"net/netip"
	"strconv"
	"strings"
	"testing"
	"time"

	"golang.org/x/net/dns/dnsmessage"
	"golang.zx2c4.com/wireguard/device"
)

var server4 = netip.MustParseAddr("198.18.26.1")
var server6 = netip.MustParseAddr("fd00:26::1")

func TestIndependentWireGuardStacks(t *testing.T) {
	a, gatewayA := loopbackPair(t, "A", false)
	b, _ := loopbackPair(t, "B", true)

	direct, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	serveTCP(t, direct, "direct")
	check(t, "direct", func() (net.Conn, error) {
		return net.DialTimeout("tcp", direct.Addr().String(), time.Second)
	})

	for _, path := range []struct {
		label  string
		tunnel *Tunnel
	}{{"A", a}, {"B", b}} {
		for _, v4 := range []struct {
			network string
			dial    func(netip.AddrPort) (net.Conn, error)
		}{{"tcp", tcpWithTimeout(t, path.tunnel)}, {"udp", path.tunnel.DialUDP}} {
			t.Run(path.label+"/"+v4.network, func(t *testing.T) {
				check(t, path.label, func() (net.Conn, error) {
					return v4.dial(netip.AddrPortFrom(server4, 4080))
				})
			})
		}
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		ip, err := path.tunnel.Lookup(ctx, "wg-probe-"+strings.ToLower(path.label)+".test.")
		cancel()
		if err != nil || len(ip) != 1 || ip[0] != map[string]string{"A": "192.0.2.17", "B": "192.0.2.18"}[path.label] {
			t.Fatalf("%s tunnel DNS: %v %v", path.label, ip, err)
		}
	}
	if c, err := a.DialUDP(netip.AddrPortFrom(server6, 4080)); err == nil {
		c.Close()
		t.Fatal("IPv4-only tunnel allowed an IPv6 target")
	}
	check(t, "B", func() (net.Conn, error) {
		return tcpWithTimeout(t, b)(netip.AddrPortFrom(server6, 4080))
	})
	check(t, "B", func() (net.Conn, error) {
		return b.DialUDP(netip.AddrPortFrom(server6, 4080))
	})

	gatewayA.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 450*time.Millisecond)
	defer cancel()
	c, err := a.DialTCP(ctx, netip.AddrPortFrom(server4, 4080))
	if err == nil {
		c.Close()
		t.Fatal("stopped endpoint answered")
	}
	check(t, "B", func() (net.Conn, error) {
		return b.DialUDP(netip.AddrPortFrom(server4, 4080))
	})
	check(t, "direct", func() (net.Conn, error) {
		return net.DialTimeout("tcp", direct.Addr().String(), time.Second)
	})
}

func tcpWithTimeout(t *testing.T, path *Tunnel) func(netip.AddrPort) (net.Conn, error) {
	return func(addr netip.AddrPort) (net.Conn, error) {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		return path.DialTCP(ctx, addr)
	}
}

func check(t *testing.T, want string, dial func() (net.Conn, error)) {
	t.Helper()
	c, err := dial()
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	if err := c.SetDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Write([]byte("probe")); err != nil {
		t.Fatal(err)
	}
	result := make([]byte, len(want))
	if _, err := io.ReadFull(c, result); err != nil || string(result) != want {
		t.Fatalf("got %q, want %q: %v", result, want, err)
	}
}

func loopbackPair(t *testing.T, label string, dualStack bool) (*Tunnel, *device.Device) {
	t.Helper()
	serverKey, clientKey := key(t), key(t)
	client4 := netip.MustParseAddr("198.18.26.2")
	addrs, clients := []netip.Addr{server4}, []netip.Addr{client4}
	cfg := fmt.Sprintf("private_key=%x\npublic_key=%x\nallowed_ip=%s/32\n",
		serverKey.Bytes(), clientKey.PublicKey().Bytes(), client4)
	if dualStack {
		client6 := netip.MustParseAddr("fd00:26::2")
		addrs, clients = append(addrs, server6), append(clients, client6)
		cfg += fmt.Sprintf("allowed_ip=%s/128\n", client6)
	}
	stack, dev, err := openStack(addrs, nil, cfg)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(dev.Close)
	port := fixturePort(t, dev)
	for _, addr := range addrs {
		target := netip.AddrPortFrom(addr, 4080)
		tcp, err := stack.ListenTCPAddrPort(target)
		if err != nil {
			t.Fatal(err)
		}
		serveTCP(t, tcp, label)
		udp, err := stack.ListenUDPAddrPort(target)
		if err != nil {
			t.Fatal(err)
		}
		serveUDP(t, udp, func([]byte) []byte { return []byte(label) })
	}
	dns, err := stack.ListenUDPAddrPort(netip.AddrPortFrom(server4, 53))
	if err != nil {
		t.Fatal(err)
	}
	resolved := [4]byte{192, 0, 2, 17}
	if label == "B" {
		resolved[3] = 18
	}
	serveUDP(t, dns, func(data []byte) []byte { return dnsAnswer(data, resolved, dualStack) })
	if dualStack {
		dnsTCP, err := stack.ListenTCPAddrPort(netip.AddrPortFrom(server4, 53))
		if err != nil {
			t.Fatal(err)
		}
		acceptTCP(t, dnsTCP, func(c net.Conn) {
			var length [2]byte
			if _, err := io.ReadFull(c, length[:]); err != nil {
				return
			}
			query := make([]byte, binary.BigEndian.Uint16(length[:]))
			if _, err := io.ReadFull(c, query); err != nil {
				return
			}
			if reply := dnsAnswer(query, resolved, false); reply != nil {
				binary.BigEndian.PutUint16(length[:], uint16(len(reply)))
				c.Write(append(length[:], reply...))
			}
		})
	}

	tunnel, err := Open(Config{
		Addresses:  clients,
		Resolvers:  []netip.Addr{server4},
		PrivateKey: hex.EncodeToString(clientKey.Bytes()),
		PeerKey:    hex.EncodeToString(serverKey.PublicKey().Bytes()),
		Endpoint:   netip.AddrPortFrom(netip.MustParseAddr("127.0.0.1"), port),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(tunnel.Close)
	return tunnel, dev
}

func fixturePort(t *testing.T, dev *device.Device) uint16 {
	t.Helper()
	settings, err := dev.IpcGet()
	if err != nil {
		t.Fatal("could not read test gateway port")
	}
	for _, line := range strings.Split(settings, "\n") {
		value, ok := strings.CutPrefix(line, "listen_port=")
		if ok {
			port, err := strconv.ParseUint(value, 10, 16)
			if err == nil && port != 0 {
				return uint16(port)
			}
		}
	}
	t.Fatal("no test gateway port")
	return 0
}

func key(t *testing.T) *ecdh.PrivateKey {
	t.Helper()
	key, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	return key
}

func serveTCP(t *testing.T, listener net.Listener, reply string) {
	t.Helper()
	acceptTCP(t, listener, func(c net.Conn) {
		if _, err := io.ReadFull(c, make([]byte, 5)); err == nil {
			c.Write([]byte(reply))
		}
	})
}

func acceptTCP(t *testing.T, listener net.Listener, respond func(net.Conn)) {
	t.Helper()
	done := make(chan struct{})
	t.Cleanup(func() { listener.Close(); <-done })
	go func() {
		defer close(done)
		for {
			c, err := listener.Accept()
			if err != nil {
				return
			}
			c.SetDeadline(time.Now().Add(2 * time.Second))
			respond(c)
			c.Close()
		}
	}()
}

func serveUDP(t *testing.T, socket net.PacketConn, reply func([]byte) []byte) {
	t.Helper()
	done := make(chan struct{})
	t.Cleanup(func() { socket.Close(); <-done })
	go func() {
		defer close(done)
		buf := make([]byte, 512)
		for {
			n, source, err := socket.ReadFrom(buf)
			if err != nil {
				return
			}
			if data := reply(buf[:n]); data != nil {
				socket.WriteTo(data, source)
			}
		}
	}()
}

func dnsAnswer(query []byte, ip [4]byte, truncated bool) []byte {
	var request dnsmessage.Message
	if request.Unpack(query) != nil || len(request.Questions) != 1 {
		return nil
	}
	q := request.Questions[0]
	response := dnsmessage.Message{
		Header:    dnsmessage.Header{ID: request.ID, Response: true, RecursionAvailable: true, Truncated: truncated},
		Questions: request.Questions,
	}
	if !truncated && q.Type == dnsmessage.TypeA {
		response.Answers = []dnsmessage.Resource{{
			Header: dnsmessage.ResourceHeader{Name: q.Name, Type: q.Type, Class: q.Class, TTL: 1},
			Body:   &dnsmessage.AResource{A: ip},
		}}
	}
	buf, _ := response.Pack()
	return buf
}
