package adaptiveconnect

import (
	"context"
	"crypto/tls"
	"errors"
	"net/netip"
	"strings"
	"time"
)

// AddressRequest describes one explicitly authorized server, not a CIDR
// range or discovery target. The name remains the TLS certificate identity.
type AddressRequest struct {
	Host string
	IP netip.Addr
	Consent bool
}

type AddressReport struct {
	Version int `json:"version"`
	Kind string `json:"kind"`
	Scope DiagnosticScope `json:"scope"`
	Host string `json:"host"`
	CheckedAt time.Time `json:"checkedAt"`
	Complete bool `json:"complete"`
	Address *AddressConnectivity `json:"address,omitempty"`
}

// CheckAddress performs a single pinned HTTPS check without resolving the
// test hostname. No network range scanning, DNS inference, or routing change.
func (t *SiberianTester) CheckAddress(ctx context.Context,path DiagnosticPath,req AddressRequest)(AddressReport,error) {
	host:=strings.ToLower(strings.TrimSpace(req.Host))
	if !req.Consent||path==nil||!diagnosticHost(host)||!publicDiagnosticIP(req.IP)||req.IP.Is4In6() {
		return AddressReport{},errors.New("explicit consent, owned hostname and one public IP required")
	}
	scope,generation:=path.Scope(),path.Generation()
	if generation==0||(scope!=ScopePhysical&&scope!=ScopeTunnel) {return AddressReport{},errors.New("unverified diagnostic path")}
	if err:=ctx.Err();err!=nil {return AddressReport{},err}
	if err:=t.begin();err!=nil {return AddressReport{},err}
	defer t.finish()
	parent:=ctx
	ctx,cancel:=context.WithTimeout(parent,5*time.Second)
	defer cancel()
	r:=AddressReport{Version:1,Kind:"address-connectivity",Scope:scope,Host:host,CheckedAt:time.Now().UTC()}
	if path.Generation()!=generation||path.Scope()!=scope {return r,errors.New("diagnostic path changed")}
	address:=connectivityAddress(ctx,path,req.IP,host,&tls.Config{MinVersion:tls.VersionTLS12})
	r.Address=&address
	if path.Generation()!=generation||path.Scope()!=scope {return r,errors.New("diagnostic path changed")}
	// A bounded endpoint timeout is itself a measured result; external
	// cancellation is not a complete observation.
	if err:=parent.Err();err!=nil {return r,err}
	if address.TCP.Outcome=="canceled"||address.TLS.Outcome=="canceled"||address.HTTP.Outcome=="canceled" {
		return r,context.Canceled
	}
	r.Complete=true
	return r,nil
}
