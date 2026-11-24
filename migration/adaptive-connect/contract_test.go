package adaptiveconnect

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Serialize actual runtime types, including nil slices, omitted pointers,
// nanosecond timestamps and optional HTTP status. All data is synthetic.
func TestExportDiagnosticContractFixtures(t *testing.T) {
	at:=time.Date(2026,11,25,10,0,0,123456789,time.UTC)
	ok:=DiagnosticAttempt{Stage:"tls",Outcome:"ok",DurationMS:25}
	timeout:=DiagnosticAttempt{Stage:"tls",Outcome:"timeout",DurationMS:4000}
	fixtures:=map[string]any{}
	for _,name:=range []string{"go-standard","utls-chrome-auto","utls-firefox-auto","utls-safari-auto"} {
		r:=SiberianReport{Version:1,Scope:ScopePhysical,Host:"test.example.com",ControlHost:"control.example.com",IP:"1.1.1.1",Fingerprint:name,CheckedAt:at,Baseline:[]DiagnosticAttempt{ok,ok},Burst:[]DiagnosticAttempt{timeout,ok,ok,ok},Control:&ok,Evidence:"asymmetry"}
		fixtures["siberian-"+name+".json"]=r
	}
	fixtures["siberian-partial.json"]=SiberianReport{Version:1,Scope:ScopeTunnel,Host:"test.example.com",ControlHost:"control.example.com",Fingerprint:"go-standard",CheckedAt:at,Evidence:"inconclusive"}
	pass:=ConnectivityStage{Outcome:"ok",DurationMS:12}
	failed:=ConnectivityStage{Outcome:"timeout",DurationMS:4000}
	skipped:=ConnectivityStage{Outcome:"not-tested"}
	a:=AddressConnectivity{IP:"1.1.1.1",TCP:pass,TLS:pass,HTTP:pass,HTTPStatus:503}
	b:=AddressConnectivity{IP:"1.1.1.1",TCP:failed,TLS:skipped,HTTP:skipped}
	fixtures["connectivity.json"]=ConnectivityReport{Version:1,Kind:"connectivity",Scope:ScopePhysical,Host:"test.example.com",CheckedAt:at,Complete:true,Families:[]FamilyConnectivity{
		{Family:"ip4",DNS:pass,Resolved:1,Addresses:[]AddressConnectivity{a}},
		{Family:"ip6",DNS:ConnectivityStage{Outcome:"no-address",DurationMS:8},Addresses:[]AddressConnectivity{}},
	}}
	fixtures["connectivity-partial.json"]=ConnectivityReport{Version:1,Kind:"connectivity",Scope:ScopeTunnel,Host:"test.example.com",CheckedAt:at,Families:[]FamilyConnectivity{}}
	fixtures["address-physical.json"]=AddressReport{Version:1,Kind:"address-connectivity",Scope:ScopePhysical,Host:"test.example.com",CheckedAt:at,Complete:true,Address:&b}
	fixtures["address-tunnel.json"]=AddressReport{Version:1,Kind:"address-connectivity",Scope:ScopeTunnel,Host:"test.example.com",CheckedAt:at,Complete:true,Address:&a}
	fixtures["address-partial.json"]=AddressReport{Version:1,Kind:"address-connectivity",Scope:ScopeTunnel,Host:"test.example.com",CheckedAt:at}
	fixtures["core-policy.json"]=DefaultConfig()
	dir:=os.Getenv("VETROFF_CONTRACT_DIR")
	for name,value:=range fixtures {
		raw,err:=json.MarshalIndent(value,"","  ")
		if err!=nil||len(raw)>65535||!json.Valid(raw) {t.Fatal(name,err)}
		if dir!="" {
			if err=os.WriteFile(filepath.Join(dir,name),append(raw,'\n'),0600);err!=nil {t.Fatal(name,err)}
		}
	}
}

// Reverse direction: the actual panel exporter supplies the JSON; the
// strict runtime parser must accept it without a separate JS schema copy.
func TestPanelPolicyContract(t *testing.T) {
	dir:=os.Getenv("VETROFF_CONTRACT_DIR")
	if dir=="" {t.Skip("cross-repository policy files not requested")}
	cases:=map[string]Config{
		"panel-default.json":DefaultConfig(),
		"panel-enabled.json":{Version:1,Enabled:true,FailureThreshold:10,RecoveryThreshold:1,CooldownSeconds:0,MaxAgeSeconds:600,Failback:true},
	}
	for name,want:=range cases {
		raw,err:=os.ReadFile(filepath.Join(dir,name))
		if err!=nil {t.Fatal(name,err)}
		got,err:=ParseConfig(raw)
		if err!=nil||got!=want {t.Fatal(name,got,err)}
	}
}
