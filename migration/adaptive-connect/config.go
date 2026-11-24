package adaptiveconnect

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"time"
)

const ConfigVersion = 1
const MaxConfigBytes = 4096

// Config is a portable policy, never an authorization to enable the runtime.
// It contains no destinations, credentials or network identifiers.
type Config struct {
	Version int `json:"version"`
	Enabled bool `json:"enabled"`
	FailureThreshold int `json:"failureThreshold"`
	RecoveryThreshold int `json:"recoveryThreshold"`
	CooldownSeconds int `json:"cooldownSeconds"`
	MaxAgeSeconds int `json:"maxAgeSeconds"`
	Failback bool `json:"failback"`
}

func DefaultConfig() Config {
	return Config{
		Version:ConfigVersion, FailureThreshold:2, RecoveryThreshold:3,
		CooldownSeconds:30, MaxAgeSeconds:90,
	}
}

func (c Config) Validate() error {
	if c.Version != ConfigVersion { return errors.New("unsupported Adaptive Connect version") }
	if c.FailureThreshold < 1 || c.FailureThreshold > 10 ||
		c.RecoveryThreshold < 1 || c.RecoveryThreshold > 10 ||
		c.CooldownSeconds < 0 || c.CooldownSeconds > 300 ||
		c.MaxAgeSeconds < 5 || c.MaxAgeSeconds > 600 {
		return errors.New("Adaptive Connect policy outside supported limits")
	}
	return nil
}

func (c Config) Policy() (Policy,error) {
	if err:=c.Validate();err!=nil { return Policy{},err }
	return Policy{
		FailureThreshold:c.FailureThreshold, RecoveryThreshold:c.RecoveryThreshold,
		Cooldown:time.Duration(c.CooldownSeconds)*time.Second,
		MaxAge:time.Duration(c.MaxAgeSeconds)*time.Second, Failback:c.Failback,
	},nil
}

// ParseConfig rejects unknown, duplicated, missing and null fields and trailing
// documents. Field names are case-sensitive. No partial policy is returned.
func ParseConfig(raw []byte) (Config,error) {
	if len(raw)==0 || len(raw)>MaxConfigBytes {
		return Config{},errors.New("invalid Adaptive Connect config size")
	}
	d:=json.NewDecoder(bytes.NewReader(raw))
	start,err:=d.Token()
	if err!=nil || start!=json.Delim('{') { return Config{},errors.New("expected policy object") }
	var c Config
	fields:=map[string]any{
		"version":&c.Version,"enabled":&c.Enabled,"failureThreshold":&c.FailureThreshold,
		"recoveryThreshold":&c.RecoveryThreshold,"cooldownSeconds":&c.CooldownSeconds,
		"maxAgeSeconds":&c.MaxAgeSeconds,"failback":&c.Failback,
	}
	for d.More() {
		key,err:=d.Token()
		if err!=nil { return Config{},err }
		name,ok:=key.(string)
		if !ok { return Config{},errors.New("invalid key") }
		target,ok:=fields[name]
		if !ok { return Config{},fmt.Errorf("unknown or duplicate field %q",name) }
		var value json.RawMessage
		if err=d.Decode(&value);err!=nil { return Config{},err }
		if bytes.Equal(bytes.TrimSpace(value),[]byte("null")) {
			return Config{},fmt.Errorf("null field %q",name)
		}
		if err=json.Unmarshal(value,target);err!=nil { return Config{},fmt.Errorf("invalid %s: %w",name,err) }
		delete(fields,name)
	}
	end,err:=d.Token()
	if err!=nil || end!=json.Delim('}') || len(fields)!=0 {
		return Config{},errors.New("incomplete policy object")
	}
	var extra json.RawMessage
	if err=d.Decode(&extra);err!=io.EOF { return Config{},errors.New("trailing policy data") }
	if err=c.Validate();err!=nil { return Config{},err }
	return c,nil
}

// NewConfiguredRuntime only constructs an engine when the policy requests it
// AND the local user has explicitly opted in. nil,nil means ordinary routing
// remains in control. The caller validates resolved outbound dependencies.
func NewConfiguredRuntime(c Config, localOptIn bool, outbounds []Outbound, network string, switcher Switcher) (*Runtime,error) {
	p,err:=c.Policy()
	if err!=nil { return nil,err }
	if !c.Enabled || !localOptIn { return nil,nil }
	return NewRuntime(p,outbounds,network,true,switcher)
}
