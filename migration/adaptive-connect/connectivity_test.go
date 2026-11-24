package adaptiveconnect

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"sync/atomic"
	"testing"
)

type connectivityFake struct {
	generation atomic.Uint64
	dials atomic.Int32
	answers map[string][]netip.Addr
	address string
}

func (p *connectivityFake) Scope() DiagnosticScope { return ScopePhysical }
func (p *connectivityFake) Generation() uint64 { return p.generation.Load() }
func (p *connectivityFake) LookupNetIP(_ context.Context,family,host string)([]netip.Addr,error) {
	return p.answers[family],nil
}
func (p *connectivityFake) DialContext(ctx context.Context,network,address string)(net.Conn,error) {
	p.dials.Add(1)
	if p.address=="" { return nil,errors.New("test dial failure") }
	return (&net.Dialer{}).DialContext(ctx,network,p.address)
}

func TestConnectivityFamiliesAndSharedGate(t *testing.T) {
	p:=&connectivityFake{answers:map[string][]netip.Addr{
		"ip4":{netip.MustParseAddr("1.1.1.1"),netip.MustParseAddr("8.8.8.8"),netip.MustParseAddr("9.9.9.9")},
		"ip6":{netip.MustParseAddr("::1")},
	}}
	p.generation.Store(1)
	var tester SiberianTester
	if _,err:=tester.CheckConnectivity(context.Background(),p,ConnectivityRequest{Host:"example.com"});err==nil { t.Fatal("no consent accepted") }
	r,err:=tester.CheckConnectivity(context.Background(),p,ConnectivityRequest{"example.com",true})
	if err!=nil || !r.Complete || len(r.Families)!=2 { t.Fatal(r,err) }
	a,b:=r.Families[0],r.Families[1]
	if a.Resolved!=3 || !a.Truncated || len(a.Addresses)!=2 || b.DNS.Outcome!="invalid-answer" || p.dials.Load()!=2 { t.Fatal(r) }
	if a.Addresses[0].TCP.Outcome!="error" || a.Addresses[0].TLS.Outcome!="not-tested" { t.Fatal(a) }
	if _,err:=tester.Check(context.Background(),p,SiberianRequest{"example.com","control.example.com",true});err==nil { t.Fatal("shared cooldown not enforced") }
}

func TestConnectivityTLSAndHTTP(t *testing.T) {
	var requests atomic.Int32
	server:=httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter,r *http.Request){
		requests.Add(1)
		if r.Method!=http.MethodHead || r.URL.Path!="/" { t.Error("unexpected request") }
		w.Header().Set("Location","https://invalid.example/")
		w.WriteHeader(http.StatusFound)
	}))
	defer server.Close()
	p:=&connectivityFake{address:server.Listener.Addr().String()}
	ip:=netip.MustParseAddr("1.1.1.1")
	untrusted:=connectivityAddress(context.Background(),p,ip,"example.com",&tls.Config{MinVersion:tls.VersionTLS12})
	if untrusted.TCP.Outcome!="ok" || untrusted.TLS.Outcome!="certificate-error" || untrusted.HTTP.Outcome!="not-tested" || requests.Load()!=0 { t.Fatal(untrusted) }
	roots:=x509.NewCertPool()
	roots.AddCert(server.Certificate())
	// httptest's certificate contains example.com.
	result:=connectivityAddress(context.Background(),p,ip,"example.com",&tls.Config{MinVersion:tls.VersionTLS12,RootCAs:roots})
	if result.TLS.Outcome!="ok" || result.HTTP.Outcome!="ok" || result.HTTPStatus!=302 || requests.Load()!=1 { t.Fatal(result,requests.Load()) }
}

func TestConnectivityPathChange(t *testing.T) {
	p:=&diagnosticFake{scope:ScopePhysical,changeOnLookup:true}
	p.generation.Store(1)
	var tester SiberianTester
	r,err:=tester.CheckConnectivity(context.Background(),p,ConnectivityRequest{"example.com",true})
	if err==nil || r.Complete || p.dials.Load()!=0 { t.Fatal(r,err) }
}
