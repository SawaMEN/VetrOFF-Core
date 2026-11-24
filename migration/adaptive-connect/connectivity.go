package adaptiveconnect

import (
	"bufio"
	"context"
	"crypto/tls"
	"crypto/x509"
	"errors"
	"io"
	"net"
	"net/http"
	"net/netip"
	"strings"
	"time"
)

type ConnectivityRequest struct {
	Host string
	Consent bool
}

type ConnectivityStage struct {
	Outcome string `json:"outcome"`
	DurationMS int64 `json:"durationMs"`
}

type AddressConnectivity struct {
	IP string `json:"ip"`
	TCP ConnectivityStage `json:"tcp"`
	TLS ConnectivityStage `json:"tls"`
	HTTP ConnectivityStage `json:"http"`
	HTTPStatus int `json:"httpStatus,omitempty"`
}

type FamilyConnectivity struct {
	Family string `json:"family"`
	DNS ConnectivityStage `json:"dns"`
	Resolved int `json:"resolved"`
	Truncated bool `json:"truncated"`
	Addresses []AddressConnectivity `json:"addresses"`
}

type ConnectivityReport struct {
	Version int `json:"version"`
	Kind string `json:"kind"`
	Scope DiagnosticScope `json:"scope"`
	Host string `json:"host"`
	CheckedAt time.Time `json:"checkedAt"`
	Complete bool `json:"complete"`
	Families []FamilyConnectivity `json:"families"`
}

func connectivityOutcome(err error) string {
	if err==nil { return "ok" }
	var unknown x509.UnknownAuthorityError
	var hostname x509.HostnameError
	var invalid x509.CertificateInvalidError
	var dns *net.DNSError
	var ne net.Error
	switch {
	case errors.Is(err,context.Canceled): return "canceled"
	case errors.Is(err,context.DeadlineExceeded),errors.As(err,&ne)&&ne.Timeout(): return "timeout"
	case errors.As(err,&unknown),errors.As(err,&hostname),errors.As(err,&invalid): return "certificate-error"
	case errors.As(err,&dns)&&dns.IsNotFound: return "not-found"
	default: return "error"
	}
}

func measured(start time.Time,err error) ConnectivityStage {
	return ConnectivityStage{Outcome:connectivityOutcome(err),DurationMS:time.Since(start).Milliseconds()}
}

func connectivityAddress(ctx context.Context,path DiagnosticPath,ip netip.Addr,host string,config *tls.Config) AddressConnectivity {
	skipped:=ConnectivityStage{Outcome:"not-tested"}
	r:=AddressConnectivity{IP:ip.String(),TCP:skipped,TLS:skipped,HTTP:skipped}
	ctx,cancel:=context.WithTimeout(ctx,5*time.Second)
	defer cancel()
	start:=time.Now()
	conn,err:=path.DialContext(ctx,"tcp",net.JoinHostPort(ip.String(),"443"))
	if conn!=nil { defer conn.Close() }
	if err==nil && conn==nil { err=errors.New("missing connection") }
	r.TCP=measured(start,err)
	if r.TCP.Outcome=="certificate-error"||r.TCP.Outcome=="not-found" { r.TCP.Outcome="error" }
	if err!=nil { return r }
	// HTTP I/O is bounded by both the context and a connection deadline.
	deadline,_:=ctx.Deadline()
	if err=conn.SetDeadline(deadline);err!=nil {
		r.TLS=ConnectivityStage{Outcome:"error"}
		return r
	}
	stop:=context.AfterFunc(ctx,func(){ _=conn.Close() })
	defer stop()
	cfg:=config.Clone()
	cfg.ServerName=host
	cfg.NextProtos=[]string{"http/1.1"}
	start=time.Now()
	client:=tls.Client(conn,cfg)
	err=client.HandshakeContext(ctx)
	r.TLS=measured(start,err)
	if r.TLS.Outcome=="not-found" { r.TLS.Outcome="error" }
	if err!=nil { return r }
	start=time.Now()
	// Send only a single HEAD for the explicitly owned host. No cookies,
	// credentials, response bodies, redirects, or automatic proxy fallback.
	req,err:=http.NewRequestWithContext(ctx,http.MethodHead,"https://"+host+"/",nil)
	if err==nil { err=req.Write(client) }
	if err==nil {
		var resp *http.Response
		resp,err=http.ReadResponse(bufio.NewReader(io.LimitReader(client,32*1024)),req)
		if err==nil {
			r.HTTPStatus=resp.StatusCode
			_ = resp.Body.Close()
			if resp.StatusCode<200 || resp.StatusCode>599 {
				err=errors.New("no final HTTP response")
			}
		}
	}
	r.HTTP=measured(start,err)
	if r.HTTP.Outcome=="certificate-error"||r.HTTP.Outcome=="not-found" { r.HTTP.Outcome="error" }
	return r
}

// CheckConnectivity is a bounded, read-only connectivity diagnostic inspired
// by the categories of DPI-CH, not a port scan or an upstream test port.
// Reuse the same tester instance as Check to share the consent/cooldown gate.
// The platform must guarantee that a bound path never silently falls back.
func (t *SiberianTester) CheckConnectivity(ctx context.Context,path DiagnosticPath,req ConnectivityRequest) (ConnectivityReport,error) {
	host:=strings.ToLower(strings.TrimSpace(req.Host))
	if !req.Consent || path==nil || !diagnosticHost(host) {
		return ConnectivityReport{},errors.New("explicit consent and an owned public hostname required")
	}
	scope,generation:=path.Scope(),path.Generation()
	if (scope!=ScopePhysical && scope!=ScopeTunnel)||generation==0 {
		return ConnectivityReport{},errors.New("unverified diagnostic path")
	}
	if err:=ctx.Err();err!=nil { return ConnectivityReport{},err }
	if err:=t.begin();err!=nil { return ConnectivityReport{},err }
	defer t.finish()
	ctx,cancel:=context.WithTimeout(ctx,30*time.Second)
	defer cancel()
	checkPath:=func()error{
		if err:=ctx.Err();err!=nil { return err }
		if path.Scope()!=scope || path.Generation()!=generation { return errors.New("diagnostic path changed") }
		return nil
	}
	r:=ConnectivityReport{Version:1,Kind:"connectivity",Scope:scope,Host:host,CheckedAt:time.Now().UTC(),Families:[]FamilyConnectivity{}}
	for _,family:=range []string{"ip4","ip6"} {
		if err:=checkPath();err!=nil { return r,err }
		item:=FamilyConnectivity{Family:family,Addresses:[]AddressConnectivity{}}
		start:=time.Now()
		dnsCtx,stop:=context.WithTimeout(ctx,3*time.Second)
		ips,err:=path.LookupNetIP(dnsCtx,family,host+".")
		stop()
		item.DNS=measured(start,err)
		if item.DNS.Outcome=="certificate-error" { item.DNS.Outcome="error" }
		if err:=checkPath();err!=nil { return r,err }
		if err!=nil {
			r.Families=append(r.Families,item)
			continue
		}
		// Reject the whole family if any DNS answer is unsafe or mismatched;
		// never dial a different resolution or a private address.
		if len(ips)>32 {
			item.DNS.Outcome="invalid-answer"
			r.Families=append(r.Families,item)
			continue
		}
		seen:=map[netip.Addr]bool{}
		var unique []netip.Addr
		for _,ip:=range ips {
			ip=ip.Unmap()
			if !publicDiagnosticIP(ip)||(family=="ip4")!=ip.Is4() {
				item.DNS.Outcome="invalid-answer"
				break
			}
			if !seen[ip] { seen[ip]=true;unique=append(unique,ip) }
		}
		if item.DNS.Outcome!="ok" {
			r.Families=append(r.Families,item)
			continue
		}
		item.Resolved=len(unique)
		if len(unique)==0 { item.DNS.Outcome="no-address" }
		if len(unique)>2 { item.Truncated=true;unique=unique[:2] }
		for _,ip:=range unique {
			if err:=checkPath();err!=nil {
				r.Families=append(r.Families,item)
				return r,err
			}
			item.Addresses=append(item.Addresses,connectivityAddress(ctx,path,ip,host,&tls.Config{MinVersion:tls.VersionTLS12}))
			if err:=checkPath();err!=nil {
				r.Families=append(r.Families,item)
				return r,err
			}
		}
		r.Families=append(r.Families,item)
	}
	if err:=checkPath();err!=nil { return r,err }
	r.Complete=true
	return r,nil
}
