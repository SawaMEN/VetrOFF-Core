package adaptiveconnect

import (
	"errors"
	"sort"
	"sync"
	"time"
)

// HistoryKey is an opaque process-local identity. The platform must generate
// it from a random process secret, never use raw SSIDs, IPs or credentials.
// Profile keys must change when effective outbound configuration changes.
type HistoryKey [32]byte

type HistoryPolicy struct {
	Enabled bool
	Retention time.Duration
	MaxSeries int
	MaxSamples int
}

func DefaultHistoryPolicy() HistoryPolicy {
	return HistoryPolicy{Retention:24*time.Hour,MaxSeries:128,MaxSamples:32}
}

type historySeriesKey struct {
	network HistoryKey
	profile HistoryKey
	protocol string
}

type historySample struct {
	at time.Time
	success bool
}

type HistorySummary struct {
	Samples int
	Passed int
	Failed int
	Last time.Time
}

type AvailabilityHistory struct {
	mu sync.Mutex
	policy HistoryPolicy
	series map[historySeriesKey][]historySample
}

func NewAvailabilityHistory(policy HistoryPolicy,localOptIn bool)(*AvailabilityHistory,error) {
	if policy.Retention<time.Minute || policy.Retention>7*24*time.Hour ||
		policy.MaxSeries<1 || policy.MaxSeries>256 ||
		policy.MaxSamples<1 || policy.MaxSamples>128 {
		return nil,errors.New("invalid availability history policy")
	}
	if !policy.Enabled || !localOptIn { return nil,nil }
	return &AvailabilityHistory{policy:policy,series:make(map[historySeriesKey][]historySample)},nil
}

func validHistoryKey(network,profile HistoryKey,protocol string) bool {
	return network!=(HistoryKey{}) && profile!=(HistoryKey{}) && (protocol=="tcp" || protocol=="udp")
}

// prune must be called under the lock. Do not use history as fresh evidence.
func (h *AvailabilityHistory) prune(now time.Time) {
	for key,samples:=range h.series {
		// Drop observations outside the retention window, including future
		// observations after a wall clock change.
		j:=0
		for _,s:=range samples {
			if !s.at.After(now) && now.Sub(s.at)<=h.policy.Retention {
				samples[j]=s
				j++
			}
		}
		if j==0 { delete(h.series,key) } else { h.series[key]=samples[:j] }
	}
}

func (h *AvailabilityHistory) record(now time.Time,network,profile HistoryKey,protocol string,at time.Time,success bool) bool {
	if !validHistoryKey(network,profile,protocol) || now.IsZero() || at.IsZero() ||
		at.After(now) || now.Sub(at)>h.policy.Retention { return false }
	h.mu.Lock()
	defer h.mu.Unlock()
	h.prune(now)
	key:=historySeriesKey{network,profile,protocol}
	samples:=h.series[key]
	if len(samples)>0 && !at.After(samples[len(samples)-1].at) { return false }
	if len(samples)==0 && len(h.series)>=h.policy.MaxSeries {
		// Evict the least recently observed series, not a random active one.
		var victim historySeriesKey
		var oldest time.Time
		for k,v:=range h.series {
			last:=v[len(v)-1].at
			if oldest.IsZero() || last.Before(oldest) { victim,oldest=k,last }
		}
		// A delayed old sample must not displace newer history.
		if !at.After(oldest) { return false }
		delete(h.series,victim)
	}
	if len(samples)==h.policy.MaxSamples {
		copy(samples,samples[1:])
		samples=samples[:len(samples)-1]
	}
	h.series[key]=append(samples,historySample{at:at,success:success})
	return true
}

func (h *AvailabilityHistory) summaryLocked(key historySeriesKey) HistorySummary {
	var result HistorySummary
	for _,s:=range h.series[key] {
		result.Samples++
		if s.success { result.Passed++ } else { result.Failed++ }
		result.Last=s.at
	}
	return result
}

func (h *AvailabilityHistory) Summary(now time.Time,network,profile HistoryKey,protocol string) HistorySummary {
	if !validHistoryKey(network,profile,protocol) || now.IsZero() { return HistorySummary{} }
	h.mu.Lock()
	defer h.mu.Unlock()
	h.prune(now)
	return h.summaryLocked(historySeriesKey{network,profile,protocol})
}

// ProbeOrder returns a stable order for existing probes only. This is never
// an outbound selection or an authorization to skip a candidate. A minimum
// of three observations is required before a series affects ordering.
func (h *AvailabilityHistory) ProbeOrder(now time.Time,network HistoryKey,protocol string,profiles []HistoryKey) []HistoryKey {
	result:=append([]HistoryKey(nil),profiles...)
	if network==(HistoryKey{}) || (protocol!="tcp" && protocol!="udp") || now.IsZero() { return result }
	h.mu.Lock()
	defer h.mu.Unlock()
	h.prune(now)
	score:=make(map[HistoryKey]HistorySummary,len(profiles))
	for _,p:=range profiles { score[p]=h.summaryLocked(historySeriesKey{network,p,protocol}) }
	// Use a neutral prior (1/2) for unknown/undersampled profiles. For known
	// profiles use Laplace smoothing, preventing one lucky observation from
	// dominating. Integer cross products avoid floating-point ties.
	ratio:=func(p HistoryKey)(int,int){
		s:=score[p]
		if s.Samples<3 { return 1,2 }
		return s.Passed+1,s.Samples+2
	}
	sort.SliceStable(result,func(i,j int)bool{
		an,ad:=ratio(result[i]);bn,bd:=ratio(result[j])
		return an*bd>bn*ad
	})
	return result
}

// Clear supports local privacy controls. No background workers or disk I/O.
func (h *AvailabilityHistory) Clear() {
	h.mu.Lock()
	defer h.mu.Unlock()
	clear(h.series)
}

// RecordWithHistory accepts only samples already accepted by Runtime.Record.
// The owner must serialize this call with network changes and derive keys
// locally from the same generation and validated effective outbound. A
// canceled/skipped probe or rejected sample never changes history.
// Failure to retain optional history never changes the live decision.
func (r *Runtime) RecordWithHistory(now time.Time,result ProbeResult,h *AvailabilityHistory,network,profile HistoryKey) bool {
	if !r.Record(now,result) { return false }
	if h!=nil {
		h.record(now,network,profile,result.Network,result.CompletedAt,result.Outcome==ProbePassed)
	}
	return true
}
