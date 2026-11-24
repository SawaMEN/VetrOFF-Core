package adaptiveconnect

import (
	"errors"
	"sync"
	"time"
)

// Session binds observations to an opaque network generation. The platform
// calls ResetNetwork on interface/default-route changes, including reconnects.
// The network identifier is intentionally not persisted or transmitted.
type Session struct {
	mu sync.Mutex
	policy Policy
	candidates []Candidate
	selector *Selector
	generation uint64
	enabled bool
	manual bool
}

type NetworkDecision struct {
	Generation uint64
	Decision
}

// NewSession creates an opt-in engine. Candidate tags must be validated by
// the outbound adapter as actual tunnel outbounds, never direct or block.
func NewSession(policy Policy, candidates []Candidate, enabled bool) (*Session, error) {
	s, err := New(policy, candidates)
	if err != nil {
		return nil, err
	}
	return &Session{
		policy: policy, candidates: append([]Candidate(nil), candidates...),
		selector: s, generation: 1, enabled: enabled,
	}, nil
}

func (s *Session) Generation() uint64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.generation
}

// ResetNetwork invalidates all observations from the previous network. An
// active tag is only a statement of actual state, not proof of connectivity.
func (s *Session) ResetNetwork(now time.Time, active string) (uint64, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	next, err := New(s.policy, s.candidates)
	if err != nil {
		return s.generation, err
	}
	if active != "" && !next.Confirm(now, active) {
		return s.generation, errors.New("invalid active candidate")
	}
	s.selector = next
	s.generation++
	return s.generation, nil
}

func (s *Session) SetManual(manual bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.manual = manual
}

// Record accepts only actual, completed probes from the current generation.
// A cancelled or skipped measurement must not be recorded as a failure.
func (s *Session) Record(generation uint64, now time.Time, sample Sample) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.enabled && generation == s.generation && s.selector.Record(now, sample)
}

func (s *Session) Decide(now time.Time) NetworkDecision {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.enabled {
		return NetworkDecision{Generation: s.generation, Decision: Decision{Reason: "disabled"}}
	}
	if s.manual {
		return NetworkDecision{Generation: s.generation, Decision: Decision{Reason: "manual-selection"}}
	}
	return NetworkDecision{Generation: s.generation, Decision: s.selector.Decide(now)}
}

// ConfirmActive acknowledges actual active state, including manual selection.
// Caller must serialize network resets and tunnel activation; a stale
// generation cannot update the new network state.
func (s *Session) ConfirmActive(generation uint64, now time.Time, active string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return generation == s.generation && s.selector.Confirm(now, active)
}

// Invalidate discards evidence after a rejected activation without counting it
// as a completed probe. All subsequent success streaks must start afresh.
func (s *Session) Invalidate(generation uint64, id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if generation != s.generation { return }
	s.selector.mu.Lock()
	defer s.selector.mu.Unlock()
	if h,ok:=s.selector.health[id];ok {
		// Preserve the timestamp watermark to reject replayed observations.
		h.successes,h.failures=0,0
		s.selector.health[id]=h
	}
}
