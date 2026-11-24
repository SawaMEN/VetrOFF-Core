package adaptiveconnect

import (
	"context"
	"errors"
	"sync"
	"time"
)

var ErrControllerInactive = errors.New("Anti-DPI controller not ready or closed")

// ControllerConfig is an internal composition, not a new wire format.
// Connect retains the existing v1 policy. Optional features require their
// own policy flag and local consent as well as Adaptive Connect consent.
type ControllerConfig struct {
	Connect Config
	MuxEnabled bool
	PreferMultiplex bool
	Pool PoolPolicy
	History HistoryPolicy
}

func DefaultControllerConfig() ControllerConfig {
	return ControllerConfig{Connect:DefaultConfig(),Pool:DefaultPoolPolicy(),History:DefaultHistoryPolicy()}
}

type ControllerConsent struct {
	Connect bool
	Mux bool
	Pool bool
	History bool
}

// RouteBinding must describe an independently routed network class and its
// selector owner. A selector shared by TCP and UDP must not be attached twice.
// ProfileKeys are opaque identities derived locally from effective options;
// unknown identities can be omitted and simply have no historical score.
type RouteBinding struct {
	Network string
	Outbounds []Outbound
	MuxPair *[2]MuxProfile
	Switcher Switcher
	ProfileKeys map[string]HistoryKey
}

type controllerRoute struct {
	runtime *Runtime
	tags map[string]bool
	keys map[string]HistoryKey
}

type Controller struct {
	mu sync.Mutex
	routes map[string]*controllerRoute
	pool *PoolBudget
	history *AvailabilityHistory
	network HistoryKey
	generation uint64
	ready bool
	closed bool
	lastReset time.Time
}

type ControllerState struct {
	Generation uint64
	Ready bool
	Closed bool
	Pool *PoolSnapshot
	HistoryEnabled bool
}

// ProbeTicket binds a worker result to this controller, network generation
// and runtime generation. The worker must retain it from before the probe.
type ProbeTicket struct {
	owner *Controller
	network string
	generation uint64
	runtimeGeneration uint64
}

func NewController(cfg ControllerConfig,consent ControllerConsent,bindings []RouteBinding)(*Controller,error) {
	policy,err:=cfg.Connect.Policy()
	if err!=nil { return nil,err }
	if !cfg.Connect.Enabled || !consent.Connect { return nil,nil }
	if len(bindings)<1 || len(bindings)>2 { return nil,errors.New("expected one or two independent route bindings") }
	c:=&Controller{routes:map[string]*controllerRoute{}}
	all:=map[string]Outbound{}
	for _,b:=range bindings {
		if c.routes[b.Network]!=nil { return nil,errors.New("duplicate route binding") }
		var r *Runtime
		if cfg.MuxEnabled && consent.Mux && b.MuxPair!=nil {
			r,err=NewMuxRuntime(MuxPolicy{Enabled:true,PreferMultiplex:cfg.PreferMultiplex,Health:cfg.Connect},true,b.MuxPair[0],b.MuxPair[1],b.Network,b.Switcher)
		} else {
			r,err=NewRuntime(policy,b.Outbounds,b.Network,true,b.Switcher)
		}
		if err!=nil { return nil,err }
		if r==nil { return nil,errors.New("missing route runtime") }
		route:=&controllerRoute{runtime:r,tags:map[string]bool{},keys:map[string]HistoryKey{}}
		for _,candidate:=range r.session.candidates {
			route.tags[candidate.ID]=true
			route.keys[candidate.ID]=b.ProfileKeys[candidate.ID]
			all[candidate.ID]=Outbound{Tag:candidate.ID,Tunnel:true}
		}
		c.routes[b.Network]=route
	}
	// No feature may create an independent selector for the same route.
	// Pool budget is shared across all independent network classes.
	outbounds:=make([]Outbound,0,len(all))
	for _,o:=range all { outbounds=append(outbounds,o) }
	c.pool,err=NewPoolBudget(cfg.Pool,consent.Pool,outbounds)
	if err!=nil { return nil,err }
	c.history,err=NewAvailabilityHistory(cfg.History,consent.History)
	if err!=nil { return nil,err }
	return c,nil
}

func (c *Controller) State() ControllerState {
	c.mu.Lock()
	defer c.mu.Unlock()
	s:=ControllerState{Generation:c.generation,Ready:c.ready&&!c.closed,Closed:c.closed,HistoryEnabled:c.history!=nil&&!c.closed}
	if c.pool!=nil { p:=c.pool.Snapshot();s.Pool=&p }
	return s
}

// ResetNetwork must be called before first use and on every path change.
// The owner supplies actual active tags (empty if none) for every binding.
// The owner also cancels old network I/O; this method rejects its late results
// and prevents stale pending leases from committing. It never closes sockets.
func (c *Controller) ResetNetwork(now time.Time,network HistoryKey,active map[string]string)(uint64,error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed { return c.generation,ErrControllerInactive }
	if now.IsZero() || now.Before(c.lastReset) || c.generation==^uint64(0) || len(active)!=len(c.routes) {
		return c.generation,errors.New("invalid network reset")
	}
	// Validate the complete reset before mutating any component.
	for name,route:=range c.routes {
		tag,ok:=active[name]
		if !ok || (tag!=""&&!route.tags[tag]) || route.runtime.Generation()==^uint64(0) {
			return c.generation,errors.New("invalid active route")
		}
	}
	if c.pool!=nil {
		if _,err:=c.pool.AdvanceNetwork();err!=nil { return c.generation,err }
	}
	for name,route:=range c.routes {
		if _,err:=route.runtime.ResetNetwork(now,active[name]);err!=nil {
			// Unexpected invariant failure: fail closed rather than use a
			// partially reset controller. The owner must rebuild it.
			c.closeLocked()
			return c.generation,err
		}
	}
	c.generation++
	c.network=network
	c.lastReset=now
	c.ready=true
	return c.generation,nil
}

func (c *Controller) BeginProbe(network string)(ProbeTicket,error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	route:=c.routes[network]
	if !c.ready||c.closed||route==nil { return ProbeTicket{},ErrControllerInactive }
	return ProbeTicket{owner:c,network:network,generation:c.generation,runtimeGeneration:route.runtime.Generation()},nil
}

func (c *Controller) Record(now time.Time,ticket ProbeTicket,tag string,completedAt time.Time,outcome ProbeOutcome) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.ready||c.closed||ticket.owner!=c||ticket.generation!=c.generation || completedAt.Before(c.lastReset) { return false }
	route:=c.routes[ticket.network]
	if route==nil||!route.tags[tag]||ticket.runtimeGeneration!=route.runtime.Generation() { return false }
	return route.runtime.RecordWithHistory(now,ProbeResult{
		Generation:ticket.runtimeGeneration,Tag:tag,Network:ticket.network,CompletedAt:completedAt,Outcome:outcome,
	},c.history,c.network,route.keys[tag])
}

// Tick is invoked by the existing selector/probe owner, never a new scheduler.
// The Switcher must not call into Controller or Runtime during activation.
func (c *Controller) Tick(ctx context.Context,network string,now time.Time)(NetworkDecision,error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	route:=c.routes[network]
	if !c.ready||c.closed||route==nil { return NetworkDecision{},ErrControllerInactive }
	if now.Before(c.lastReset) { return NetworkDecision{},errors.New("clock regression") }
	return route.runtime.Tick(ctx,now)
}

func (c *Controller) ManualSelection(network string,now time.Time,active string) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	route:=c.routes[network]
	if !c.ready||c.closed||route==nil||now.Before(c.lastReset) { return false }
	return route.runtime.ManualSelection(now,active)
}

func (c *Controller) ResumeAutomatic(network string,now time.Time,active string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	route:=c.routes[network]
	if !c.ready||c.closed||route==nil { return ErrControllerInactive }
	if now.IsZero()||now.Before(c.lastReset)||!route.tags[active]||route.runtime.Generation()==^uint64(0) {
		return errors.New("invalid automatic resume")
	}
	return route.runtime.ResumeAutomatic(now,active)
}

// Reserve checks the actual intended profile and physical network generation.
// nil,nil means the optional budget is disabled, not permission for direct
// fallback. A lease must be released by the transport owner after cleanup.
func (c *Controller) Reserve(generation uint64,network,tag string)(*Lease,error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.ready||c.closed { return nil,ErrControllerInactive }
	if generation!=c.generation { return nil,ErrPoolStale }
	route:=c.routes[network]
	if route==nil||!route.tags[tag] { return nil,errors.New("unknown route profile") }
	if c.pool==nil { return nil,nil }
	return c.pool.Reserve(c.pool.Snapshot().Generation,tag)
}

func (c *Controller) ClearHistory() {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.history!=nil { c.history.Clear() }
}

func (c *Controller) closeLocked() {
	c.closed=true
	c.ready=false
	c.network=HistoryKey{}
	if c.pool!=nil { c.pool.Close() }
	if c.history!=nil { c.history.Clear() }
}

func (c *Controller) Close() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.closeLocked()
}
