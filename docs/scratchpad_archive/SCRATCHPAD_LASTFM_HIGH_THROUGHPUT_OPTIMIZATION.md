# 🎯 Active Work Tracker - LastFM High-Throughput API Optimization

> [!info] Purpose
> This file tracks optimization work to maximize LastFM API throughput within rate limit constraints. For strategic roadmap and completed milestones, see [[BACKLOG.md]].

**Current Initiative**: LastFM High-Throughput API Optimization
**Status**: `#not-started` `#performance` `#v2.2`
**Last Updated**: 2025-08-09

## Progress Overview
- [x] **Streaming Batch Processor** ✅ (Completed - Rate limiting at task creation)
- [ ] **High-Throughput Executor** 🔜 (Not Started - Current focus)
- [ ] **Connection Optimization** 

---

## 🔜 NEW Epic: LastFM High-Throughput Executor `#not-started`

**Goal**: Maximize LastFM API throughput by allowing 100+ concurrent outstanding requests while respecting the 5 calls/second rate limit.
**Why**: LastFM's multi-second response times + 5/sec rate limit creates a throughput bottleneck. We need many outstanding requests to achieve optimal performance without "backing off" behavior.
**Effort**: S - Targeted optimization of existing architecture without over-engineering

### 🤔 Key Architectural Decision
> [!important] Separation of Rate Limiting and Concurrency Concerns
> **Key Insight**: We currently conflate two separate concerns: (1) rate limiting when to START calls, and (2) allowing many concurrent outstanding requests. The streaming batch processor handles #1 correctly, but the default ThreadPoolExecutor (8 threads) severely limits #2.
>
> **Chosen Approach**: Create a dedicated high-capacity ThreadPoolExecutor (100-200 threads) specifically for LastFM I/O-bound operations, integrated with the existing streaming batch processor pattern.
>
> **Rationale**:
> - **Optimal Throughput**: 100+ concurrent requests × 5/sec rate limit = maximum API utilization
> - **Minimal Changes**: Works with existing pylast + asyncio.to_thread() pattern
> - **Not Over-Engineered**: Targeted fix for the specific bottleneck, maintains clean architecture

### 📝 Implementation Plan
> [!note]
> Optimize the concurrency bottleneck while preserving existing architecture patterns.

**Phase 1: High-Throughput Executor Foundation**
- [ ] **Task 1.1**: Create `LastFMHighThroughputExecutor` class with 100-200 thread capacity
- [ ] **Task 1.2**: Integrate with existing `LastFMAPIClient` to replace `asyncio.to_thread()` calls
- [ ] **Task 1.3**: Add configuration for executor thread count via settings

**Phase 2: Connection and Client Optimization**
- [ ] **Task 2.1**: Ensure single `pylast.LastFMNetwork` instance reuse across operations
- [ ] **Task 2.2**: Investigate pylast connection pooling behavior and optimization opportunities
- [ ] **Task 2.3**: Add executor lifecycle management (proper shutdown on application exit)

**Phase 3: Performance Validation**
- [ ] **Task 3.1**: Benchmark concurrent request performance (target: sustained 4.8 req/sec)
- [ ] **Task 3.2**: Memory usage profiling with high thread count
- [ ] **Task 3.3**: Validate elimination of "backing off" behavior under load

### ✨ User-Facing Changes & Examples
**No user-facing changes** - this is a pure performance optimization. Users will experience:
- Faster LastFM metadata retrieval for large track collections
- No more "backing off" messages during batch operations
- More responsive progress updates during LastFM enrichment workflows

### 🛠️ Implementation Details

**Affected Architectural Layers**:
- **Domain**: No changes
- **Application**: No changes  
- **Infrastructure**: `LastFMAPIClient` executor optimization, new `LastFMHighThroughputExecutor`
- **Interface**: No changes

**Core Implementation Pattern**:
```python
# Before: Default executor (8 threads)
await asyncio.to_thread(self.client.get_track_by_mbid, mbid)

# After: High-throughput executor (100-200 threads)  
await asyncio.get_event_loop().run_in_executor(
    self.high_throughput_executor, 
    self.client.get_track_by_mbid, 
    mbid
)
```

**LastFM API Constraint Optimization**:
- **Rate Limit**: 5 calls/second (handled by existing streaming batch processor)
- **Response Time**: Multi-second delays (requires many concurrent outstanding requests)
- **Retry Handling**: Retries count against rate limit (executor handles this transparently)

**Testing Strategy**:
- **Unit**: Test executor creation, lifecycle, and thread count configuration
- **Integration**: Validate improved concurrency with mock LastFM responses of varying delays
- **Performance**: Load testing with realistic LastFM response patterns (1-5 second delays)

**Key Files to Modify**:
- `src/infrastructure/connectors/lastfm/client.py` - Executor integration
- `src/infrastructure/connectors/_shared/high_throughput_executor.py` - New executor class
- `src/config/settings.py` - Thread count configuration
- `tests/integration/test_lastfm_performance.py` - Performance validation tests

**Configuration Options**:
```python
# settings.py additions
class APISettings:
    lastfm_executor_threads: int = 150  # High capacity for I/O-bound operations
    lastfm_executor_shutdown_timeout: float = 30.0  # Graceful shutdown timeout
```

**Success Metrics**:
- 🎯 **Sustained throughput**: 4.8 requests/second under load
- 🎯 **Concurrent capacity**: 100+ outstanding requests without blocking  
- 🎯 **Memory efficiency**: Reasonable memory usage with high thread count
- 🎯 **Zero backing off**: No rate limit back-off behavior during normal operations