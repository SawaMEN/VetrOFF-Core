package adaptiveconnect

import (
	"errors"
	"strings"
	"sync"
)

var (
	ErrPoolLimit = errors.New("connection budget exhausted")
	ErrPoolStale = errors.New("obsolete network generation")
	ErrPoolClosed = errors.New("connection budget closed")
)

type PoolPolicy struct {
	Enabled bool
	MaxTotal int
	MaxPerProfile int
	MaxPending int
}

func DefaultPoolPolicy() PoolPolicy {
	return PoolPolicy{MaxTotal:64,MaxPerProfile:32,MaxPending:8}
}

// PoolBudget accounts for logical connection lifetimes, not underlying
// multiplex sockets. The existing transport owns all actual socket reuse.
// Use one budget per client, shared across selectors and TCP/UDP.
type PoolBudget struct {
	mu sync.Mutex
	policy PoolPolicy
	generation uint64
	closed bool
	total int
	pending int
	byProfile map[string]int
	profiles map[string]bool
}

type PoolSnapshot struct {
	Generation uint64
	Total int
	Pending int
	Active int
	Closed bool
	ByProfile map[string]int
}

// NewPoolBudget returns nil,nil when disabled or local consent is absent.
// The caller must use normal routing in that case, without a budget wrapper.
// Eligible profiles must be resolved and validated by the actual core.
func NewPoolBudget(policy PoolPolicy,localOptIn bool,outbounds []Outbound)(*PoolBudget,error) {
	if policy.MaxTotal<1||policy.MaxTotal>4096||
		policy.MaxPerProfile<1||policy.MaxPerProfile>policy.MaxTotal||
		policy.MaxPending<1||policy.MaxPending>policy.MaxTotal {
		return nil,errors.New("invalid connection budget limits")
	}
	if !policy.Enabled||!localOptIn { return nil,nil }
	profiles:=map[string]bool{}
	for _,o:=range outbounds {
		if !o.Tunnel||o.Hidden||strings.HasPrefix(o.Tag,"§hide§") { continue }
		if strings.TrimSpace(o.Tag)==""||strings.TrimSpace(o.Tag)!=o.Tag||profiles[o.Tag] {
			return nil,errors.New("invalid or duplicate pool profile")
		}
		profiles[o.Tag]=true
	}
	if len(profiles)<1||len(profiles)>256 { return nil,errors.New("expected 1..256 eligible profiles") }
	return &PoolBudget{policy:policy,generation:1,profiles:profiles,byProfile:map[string]int{}},nil
}

// Lease is a single reservation. Keep the pointer for the entire logical
// connection lifetime. Release on every dial error and actual connection close.
// It must not be copied. Commit/Release are serialized by the owning budget.
type Lease struct {
	budget *PoolBudget
	tag string
	generation uint64
	active bool
	released bool
}

func (p *PoolBudget) Snapshot() PoolSnapshot {
	p.mu.Lock()
	defer p.mu.Unlock()
	counts:=make(map[string]int,len(p.byProfile))
	for k,v:=range p.byProfile { counts[k]=v }
	return PoolSnapshot{Generation:p.generation,Total:p.total,Pending:p.pending,
		Active:p.total-p.pending,Closed:p.closed,ByProfile:counts}
}

// Reserve is intentionally nonblocking: it never creates a queue, goroutine,
// new socket, or alternative route. Admission failure is not a health failure.
func (p *PoolBudget) Reserve(generation uint64,tag string)(*Lease,error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed { return nil,ErrPoolClosed }
	if generation!=p.generation { return nil,ErrPoolStale }
	if !p.profiles[tag] { return nil,errors.New("unknown pool profile") }
	if p.total>=p.policy.MaxTotal||p.pending>=p.policy.MaxPending||p.byProfile[tag]>=p.policy.MaxPerProfile {
		return nil,ErrPoolLimit
	}
	p.total++
	p.pending++
	p.byProfile[tag]++
	return &Lease{budget:p,tag:tag,generation:generation},nil
}

// AdvanceNetwork invalidates pending opens from the previous generation.
// Existing reservations remain counted until the actual transport closes,
// so changing networks cannot bypass the global resource limit.
func (p *PoolBudget) AdvanceNetwork()(uint64,error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed { return p.generation,ErrPoolClosed }
	if p.generation==^uint64(0) { return p.generation,errors.New("network generation exhausted") }
	p.generation++
	return p.generation,nil
}

// Close rejects new reservations/commits. It does not close real sockets;
// the transport owner must cancel opens and close its actual connections.
func (p *PoolBudget) Close() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.closed=true
}

// Commit transitions a completed dial to active. If the generation changed,
// the owner must close the returned connection and Release the reservation.
// Failed Commit deliberately keeps the reservation charged until cleanup.
func (l *Lease) Commit() error {
	p:=l.budget
	p.mu.Lock()
	defer p.mu.Unlock()
	if l.released { return errors.New("released lease") }
	if l.active { return nil }
	if p.closed { return ErrPoolClosed }
	if l.generation!=p.generation { return ErrPoolStale }
	l.active=true
	p.pending--
	return nil
}

func (l *Lease) Release() {
	p:=l.budget
	p.mu.Lock()
	defer p.mu.Unlock()
	if l.released { return }
	l.released=true
	if !l.active { p.pending-- }
	p.total--
	p.byProfile[l.tag]--
	if p.byProfile[l.tag]==0 { delete(p.byProfile,l.tag) }
}
