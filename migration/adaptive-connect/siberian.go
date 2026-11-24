package adaptiveconnect

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"strings"
	"sync"
	"time"
)

type DiagnosticScope string

const (
	ScopePhysical DiagnosticScope = "client-physical"
	ScopeTunnel DiagnosticScope = "client-tunnel"
)

// DiagnosticPath is supplied by the platform. Both DNS and TCP must be bound
// to the same verified path. Generation changes whenever that path changes.
// No implicit fallback to the default resolver or dialer is permitted.
type DiagnosticPath interface {
	Scope() DiagnosticScope
	Generation() uint64
	LookupNetIP(ctx context.Context, network, host string) ([]netip.Addr, error)
	DialContext(ctx context.Context, network, address string) (net.Conn, error)
}

type SiberianRequest struct {
	Host string
	ControlHost string
	Consent bool // explicit local consent for this test
}

type DiagnosticAttempt struct {
	Stage string `json:"stage"`
	Outcome string `json:"outcome"`
	DurationMS int64 `json:"durationMs"`
}

type SiberianReport struct {
	Version int `json:"version"`
	Scope DiagnosticScope `json:"scope"`
	Host string `json:"host"`
	ControlHost string `json:"controlHost"`
	IP string `json:"ip"`
	Fingerprint string `json:"fingerprint"`
	CheckedAt time.Time `json:"checkedAt"`
	Baseline []DiagnosticAttempt `json:"baseline"`
	Burst []DiagnosticAttempt `json:"burst"`
	Control *DiagnosticAttempt `json:"control,omitempty"`
	Evidence string `json:"evidence"`
}

// SiberianTester runs an explicitly requested, bounded comparison inspired by
// Siberian Check. It is not an implementation of upstream fingerprint tests.
// Reuse one instance per client process to enforce the cooldown.
type SiberianTester struct {
	mu sync.Mutex
	last time.Time
	running bool
}

func diagnosticHost(host string) bool {
	if len(host)>253 || !strings.Contains(host,".") || net.ParseIP(host)!=nil {
		return false
	}
	for _,label:=range strings.Split(host,".") {
		if len(label)==0 || len(label)>63 || label[0]=='-' || label[len(label)-1]=='-' { return false }
		for _,c:=range label {
			if !((c>='a'&&c<='z')||(c>='0'&&c<='9')||c=='-') { return false }
		}
	}
	return true
}

var diagnosticExcluded = []netip.Prefix{
	netip.MustParsePrefix("100.64.0.0/10"), netip.MustParsePrefix("192.0.0.0/24"),
	netip.MustParsePrefix("192.0.2.0/24"), netip.MustParsePrefix("198.18.0.0/15"),
	netip.MustParsePrefix("198.51.100.0/24"), netip.MustParsePrefix("203.0.113.0/24"),
	netip.MustParsePrefix("240.0.0.0/4"), netip.MustParsePrefix("2001:db8::/32"),
	netip.MustParsePrefix("64:ff9b::/96"), netip.MustParsePrefix("64:ff9b:1::/48"),
	netip.MustParsePrefix("2002::/16"), netip.MustParsePrefix("2001::/32"),
}

func publicDiagnosticIP(ip netip.Addr) bool {
	ip=ip.Unmap()
	if ip.Zone()!="" || !ip.IsGlobalUnicast() || ip.IsPrivate() { return false }
	for _,prefix:=range diagnosticExcluded {
		if prefix.Contains(ip) { return false }
	}
	return true
}

func sharedDiagnosticIP(a,b []netip.Addr) (netip.Addr,error) {
	for _,list:=range [][]netip.Addr{a,b} {
		if len(list)<1 || len(list)>8 { return netip.Addr{},errors.New("expected 1..8 resolved addresses") }
		for _,ip:=range list {
			if !publicDiagnosticIP(ip) { return netip.Addr{},errors.New("non-public DNS address") }
		}
	}
	common:=map[netip.Addr]bool{}
	for _,left:=range a {
		for _,right:=range b {
			if left.Unmap()==right.Unmap() { common[left.Unmap()]=true }
		}
	}
	if len(common)!=1 { return netip.Addr{},errors.New("expected exactly one common public IP") }
	for ip:=range common { return ip,nil }
	return netip.Addr{},errors.New("no common IP")
}

func diagnosticAttempt(ctx context.Context,path DiagnosticPath,ip netip.Addr,host string) DiagnosticAttempt {
	start:=time.Now()
	ctx,cancel:=context.WithTimeout(ctx,4*time.Second)
	defer cancel()
	a:=DiagnosticAttempt{Stage:"tcp",Outcome:"ok"}
	conn,err:=path.DialContext(ctx,"tcp",net.JoinHostPort(ip.String(),"443"))
	if err==nil {
		if conn==nil {
			err=errors.New("path returned no connection")
		} else {
			defer conn.Close()
			a.Stage="tls"
			client:=tls.Client(conn,&tls.Config{ServerName:host,MinVersion:tls.VersionTLS12})
			err=client.HandshakeContext(ctx)
		}
	}
	if err!=nil {
		a.Outcome="error"
		var ne net.Error
		switch {
		case errors.Is(err,context.Canceled):
			a.Outcome="canceled"
		case errors.Is(err,context.DeadlineExceeded),errors.As(err,&ne)&&ne.Timeout():
			a.Outcome="timeout"
		}
	}
	a.DurationMS=time.Since(start).Milliseconds()
	return a
}

func classifyDiagnostic(r SiberianReport) string {
	if len(r.Baseline)!=2 || len(r.Burst)!=4 || r.Control==nil { return "inconclusive" }
	for _,a:=range r.Baseline {
		if a.Stage!="tls" || a.Outcome!="ok" { return "inconclusive" }
	}
	if r.Control.Stage!="tls" || r.Control.Outcome!="ok" { return "inconclusive" }
	timeout:=false
	for _,a:=range r.Burst {
		if a.Stage!="tls" { return "inconclusive" }
		switch a.Outcome {
		case "timeout": timeout=true
		case "ok":
		default: return "inconclusive"
		}
	}
	if timeout { return "asymmetry" }
	return "no-signal"
}

func (t *SiberianTester) Check(ctx context.Context,path DiagnosticPath,req SiberianRequest) (SiberianReport,error) {
	host:=strings.ToLower(strings.TrimSpace(req.Host))
	control:=strings.ToLower(strings.TrimSpace(req.ControlHost))
	if !req.Consent || path==nil || !diagnosticHost(host) || !diagnosticHost(control) || host==control {
		return SiberianReport{},errors.New("explicit consent and two distinct owned test hostnames required")
	}
	scope:=path.Scope()
	generation:=path.Generation()
	if (scope!=ScopePhysical && scope!=ScopeTunnel) || generation==0 {
		return SiberianReport{},errors.New("unverified diagnostic path")
	}
	if err:=ctx.Err();err!=nil { return SiberianReport{},err }
	t.mu.Lock()
	if t.running || (!t.last.IsZero() && time.Since(t.last)<time.Minute) {
		t.mu.Unlock()
		return SiberianReport{},errors.New("diagnostic busy or in cooldown")
	}
	t.running=true
	t.last=time.Now()
	t.mu.Unlock()
	defer func(){t.mu.Lock();t.running=false;t.mu.Unlock()}()
	ctx,cancel:=context.WithTimeout(ctx,28*time.Second)
	defer cancel()
	checkPath:=func() error {
		if err:=ctx.Err();err!=nil { return err }
		if path.Generation()!=generation || path.Scope()!=scope { return errors.New("diagnostic path changed") }
		return nil
	}
	r:=SiberianReport{Version:1,Scope:scope,Host:host,ControlHost:control,Fingerprint:"go-standard",CheckedAt:time.Now().UTC(),Evidence:"inconclusive"}
	a,err:=path.LookupNetIP(ctx,"ip",host+".")
	if err!=nil { return r,fmt.Errorf("target DNS: %w",err) }
	if err=checkPath();err!=nil { return r,err }
	b,err:=path.LookupNetIP(ctx,"ip",control+".")
	if err!=nil { return r,fmt.Errorf("control DNS: %w",err) }
	if err=checkPath();err!=nil { return r,err }
	ip,err:=sharedDiagnosticIP(a,b)
	if err!=nil { return r,err }
	r.IP=ip.String()
	for _,name:=range []string{host,control} {
		if err=checkPath();err!=nil { return r,err }
		r.Baseline=append(r.Baseline,diagnosticAttempt(ctx,path,ip,name))
	}
	if err=checkPath();err!=nil { return r,err }
	for _,a:=range r.Baseline {
		if a.Outcome!="ok" { return r,nil }
	}
	r.Burst=make([]DiagnosticAttempt,4)
	var wg sync.WaitGroup
	for i:=range r.Burst {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			if checkPath()!=nil {
				r.Burst[i]=DiagnosticAttempt{Stage:"tcp",Outcome:"canceled"}
				return
			}
			r.Burst[i]=diagnosticAttempt(ctx,path,ip,host)
		}(i)
	}
	wg.Wait()
	if err=checkPath();err!=nil { return r,err }
	result:=diagnosticAttempt(ctx,path,ip,control)
	r.Control=&result
	if err=checkPath();err!=nil { return r,err }
	r.Evidence=classifyDiagnostic(r)
	return r,nil
}
