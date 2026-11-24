package adaptiveconnect

import (
	"os"
	"strings"
	"testing"
	"time"
)

func TestPolicyFixture(t *testing.T) {
	raw,err:=os.ReadFile("testdata/policy-v1.json")
	if err!=nil { t.Fatal(err) }
	c,err:=ParseConfig(raw)
	if err!=nil || c!=DefaultConfig() { t.Fatal(c,err) }
	p,err:=c.Policy()
	if err!=nil || p!=DefaultPolicy() { t.Fatal(p,err) }
}

func TestRejectInvalidPolicy(t *testing.T) {
	raw,err:=os.ReadFile("testdata/policy-v1.json")
	if err!=nil { t.Fatal(err) }
	s:=string(raw)
	cases:=[]string{
		"", "null", "[]", "{}", s+"{}", strings.Repeat(" ",MaxConfigBytes+1),
		strings.Replace(s,`"version": 1`,`"version": 2`,1),
		strings.Replace(s,`"version": 1`,`"version": 1, "version": 1`,1),
		strings.Replace(s,`"enabled": false`,`"Enabled": false`,1),
		strings.Replace(s,`"enabled": false`,`"enabled": null`,1),
		strings.Replace(s,`"enabled": false,`,"",1),
		strings.Replace(s,`"enabled": false`,`"enabled": "false"`,1),
		strings.Replace(s,`"maxAgeSeconds": 90`,`"maxAgeSeconds": 4`,1),
		strings.Replace(s,`"cooldownSeconds": 30`,`"cooldownSeconds": 301`,1),
		strings.Replace(s,`"failureThreshold": 2`,`"failureThreshold": 2.5`,1),
		strings.Replace(s,`"recoveryThreshold": 3`,`"recoveryThreshold": 0`,1),
		strings.Replace(s,`"failback": false`,`"failback": false, "fallback": "direct"`,1),
	}
	for _,raw:=range cases {
		if _,err:=ParseConfig([]byte(raw));err==nil { t.Errorf("accepted %.120q",raw) }
	}
}

func TestLocalOptInRequired(t *testing.T) {
	c:=DefaultConfig()
	for _,enabled:=range []bool{false,true} {
		c.Enabled=enabled
		for _,local:=range []bool{false,true} {
			r,err:=NewConfiguredRuntime(c,local,[]Outbound{{Tag:"a",Tunnel:true,Networks:[]string{"tcp"}}},"tcp",&fakeSwitcher{})
			if err!=nil || (r!=nil)!=(enabled&&local) { t.Fatal(enabled,local,r,err) }
		}
	}
	c.CooldownSeconds=300
	c.MaxAgeSeconds=600
	p,err:=c.Policy()
	if err!=nil || p.Cooldown!=5*time.Minute || p.MaxAge!=10*time.Minute { t.Fatal(p,err) }
}
