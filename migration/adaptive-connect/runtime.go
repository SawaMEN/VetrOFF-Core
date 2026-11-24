package adaptiveconnect

import (
	"context"
	"errors"
	"strings"
	"sync"
	"time"
)

// Outbound describes a candidate already resolved by the core's outbound
// manager. Tunnel must be set only after validating the actual outbound type,
// including its dependency chain; never infer this from a user-facing tag.
type Outbound struct {
	Tag string
	Priority int
	Tunnel bool
	Hidden bool
	Networks []string
}

type ProbeOutcome uint8

const (
	ProbeSkipped ProbeOutcome = iota
	ProbeCanceled
	ProbeFailed
	ProbePassed
)

// ProbeResult is emitted by an existing end-to-end probe worker, not by a
// TCP dial, cached latency entry, or direct connectivity check.
type ProbeResult struct {
	Generation uint64
	Tag string
	Network string
	CompletedAt time.Time
	Outcome ProbeOutcome
}

// Switcher is implemented by the runtime's selector owner. It must compare
// the actual active tag with expected, verify the target through the target
// tunnel, and commit the selection only on success. On error the previous
// selection must remain unchanged. It must not call back into Runtime while
// SwitchVerified is executing. Respect context cancellation.
type Switcher interface {
	SwitchVerified(ctx context.Context, expected, target string) error
}

// Runtime is a candidate adapter for the existing URLTest worker and Selector.
// Create one instance per independently routed network class; no new probe
// scheduler is introduced. Callbacks that mutate selection are serialized.
type Runtime struct {
	mu sync.Mutex
	session *Session
	network string
	switcher Switcher
	switchTimeout time.Duration
}

func NewRuntime(policy Policy, outbounds []Outbound, network string, enabled bool, switcher Switcher) (*Runtime, error) {
	if network != "tcp" && network != "udp" {
		return nil, errors.New("unsupported network")
	}
	if switcher == nil {
		return nil, errors.New("missing selector adapter")
	}
	var candidates []Candidate
	for _, outbound := range outbounds {
		if !outbound.Tunnel || outbound.Hidden || strings.HasPrefix(outbound.Tag, "§hide§") {
			continue
		}
		for _, n := range outbound.Networks {
			if n == network {
				candidates = append(candidates, Candidate{ID:outbound.Tag, Priority:outbound.Priority})
				break
			}
		}
	}
	session, err := NewSession(policy, candidates, enabled)
	if err != nil {
		return nil, err
	}
	return &Runtime{session:session, network:network, switcher:switcher, switchTimeout:5*time.Second}, nil
}

func (r *Runtime) Generation() uint64 {
	return r.session.Generation()
}

func (r *Runtime) ResetNetwork(now time.Time, active string) (uint64, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.session.ResetNetwork(now, active)
}

// ManualSelection reports an already executed manual selection. The runtime
// owner must serialize its real manual switch with Tick, ResetNetwork and
// shutdown. Automatic decisions remain suppressed until ResumeAutomatic.
func (r *Runtime) ManualSelection(now time.Time, active string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.session.SetManual(true)
	return r.session.ConfirmActive(r.session.Generation(), now, active)
}

func (r *Runtime) ResumeAutomatic(now time.Time, active string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	// Discard observations gathered before the user's manual selection.
	if _, err := r.session.ResetNetwork(now, active); err != nil {
		return err
	}
	r.session.SetManual(false)
	return nil
}

func (r *Runtime) Record(now time.Time, result ProbeResult) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	if result.Network != r.network || (result.Outcome != ProbePassed && result.Outcome != ProbeFailed) {
		return false
	}
	return r.session.Record(result.Generation, now, Sample{
		ID:result.Tag, At:result.CompletedAt, Success:result.Outcome == ProbePassed,
	})
}

// Tick consumes existing evidence. If selection fails, no success is
// acknowledged. Evidence is invalidated and fresh evidence is required
// before retrying that candidate. No direct fallback is generated.
func (r *Runtime) Tick(ctx context.Context, now time.Time) (NetworkDecision, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return NetworkDecision{}, err
	}
	d := r.session.Decide(now)
	if d.Target == "" {
		return d, nil
	}
	ctx, cancel := context.WithTimeout(ctx, r.switchTimeout)
	defer cancel()
	if err := r.switcher.SwitchVerified(ctx, d.Current, d.Target); err != nil {
		// Failed or canceled switch is not necessarily a network failure, but
		// invalidate its earlier successful streak to avoid repeated attempts.
		r.session.Invalidate(d.Generation, d.Target)
		return d, err
	}
	if !r.session.ConfirmActive(d.Generation, now, d.Target) {
		return d, errors.New("verified selection could not be acknowledged")
	}
	return d, nil
}
