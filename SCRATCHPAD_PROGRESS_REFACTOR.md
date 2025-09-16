# Progress Tracking System - 2025 Status Report

**Current Status**: 🔄 **ARCHITECTURE REVISION NEEDED** - Competing console systems breaking progress bar pinning

## September 2024 - January 2025 Progress

**Architecture Implemented**: Event-driven progress system with clean separation
- ✅ `ProgressEmitter` protocol → `AsyncProgressManager` → `RichProgressProvider`
- ✅ All tests passing (10/10 integration, 33/33 unit tests)
- ✅ Zero type checking errors
- ✅ Fixed critical logging destruction bug that was destroying file logging during Live Display
- ✅ Created `TerminalDisplayManager` for unified CLI progress integration
- ✅ Enhanced `RichProgressProvider` to work with or without `TerminalDisplayManager`

**Problem Discovered (September 2024)**: Competing Console Systems
- ❌ **Progress bars not staying pinned** - reprinting after every log message instead of staying stationary
- ❌ **Multiple Rich console systems competing**:
  - Our app logs via RichHandler → Live Display console
  - Prefect 3.0 logs via PrefectConsoleHandler → Direct stdout/stderr
- ❌ **TerminalDisplayManager approach insufficient** - doesn't solve multi-process logging coordination

## September 2024 Research: Prefect 3.0 + Rich Integration

**Key Discovery**: Prefect 3.0 (released Sept 2024) has native Rich integration via `PrefectConsoleHandler`

**Research Findings**:
- ✅ **Progress artifacts built-in**: Prefect 3.0 includes `create_progress_artifact()` for UI progress tracking
- ✅ **Rich console handler**: `PrefectConsoleHandler` with Rich highlighting and custom styling
- ✅ **Handler customization**: Can replace Prefect's console handlers with custom Rich handlers
- ✅ **Progress.console pattern**: Rich documentation confirms Progress has built-in console for log coordination

**Root Cause Identified**: Two Separate Rich Console Systems
```
Our App Logs:    RichHandler(console=live_console) → TerminalDisplayManager Live Display
Prefect Logs:    PrefectConsoleHandler             → Direct stdout/stderr ❌
```

**The Problem**: Even though we route our logs through Rich Live Display, Prefect's separate console handler bypasses our coordination and breaks the pinned progress bar.

## REVISED PLAN: Progress.console + Prefect Handler Interception (September 2024)

**New Strategy**: Use Rich's built-in Progress.console coordination + intercept Prefect's console handler

### Why This Approach
1. **Rich Progress has built-in console**: Specifically designed for log + progress coordination
2. **Simpler architecture**: No need for complex TerminalDisplayManager
3. **Prefect 3.0 compatibility**: Leverage Prefect's configurable logging architecture
4. **Single console source of truth**: Progress.console handles everything

### Implementation Steps

#### Step 1: Replace TerminalDisplayManager with Progress.console
```python
# Instead of complex Live Display coordination:
class RichProgressProvider:
    def __init__(self):
        # Let Progress manage its own Live Display
        self._progress = Progress()  # Progress handles Live Display internally

    def get_console(self):
        # Expose Progress.console for logging coordination
        return self._progress.console
```

#### Step 2: Configure All Loggers to Use Progress.console
```python
# src/config/logging.py
def configure_unified_logging(progress_console: Console) -> None:
    """Route ALL logging through Progress.console for coordination."""

    # 1. Configure Loguru with RichHandler using Progress.console
    logger.remove()  # Clear existing handlers
    rich_handler = RichHandler(console=progress_console)
    logger.add(rich_handler, level="INFO", format="{message}")

    # 2. Intercept Prefect's console handler
    prefect_logger = logging.getLogger("prefect")
    # Remove Prefect's PrefectConsoleHandler
    for handler in prefect_logger.handlers[:]:
        if isinstance(handler, PrefectConsoleHandler):
            prefect_logger.removeHandler(handler)

    # Add our RichHandler for Prefect logs
    prefect_logger.addHandler(rich_handler)
```

#### Step 3: Update Progress Provider Integration
```python
# src/interface/cli/progress_provider.py
class RichProgressProvider:
    async def start_display(self):
        # Configure ALL logging to use our Progress.console
        configure_unified_logging(self._progress.console)

        # Start Progress (it handles its own Live Display)
        self._progress.start()
```

#### Step 4: Simplify CLI Integration
```python
# src/interface/cli/console.py
@contextlib.asynccontextmanager
async def live_display_context(show_live: bool = True):
    if not show_live:
        yield SimpleContext()
        return

    # Use Progress.console directly - no TerminalDisplayManager needed
    progress_provider = RichProgressProvider()

    try:
        await progress_provider.start_display()
        yield progress_provider  # Exposes both progress and console
    finally:
        await progress_provider.stop_display()
```

## Expected Result with New Approach
```
[09/14/25 17:23:56] INFO     Workflow starting              workflow.py:123
[09/14/25 17:23:56] DEBUG    Processing playlist items      source_nodes.py:167
[09/14/25 17:23:56] INFO     Task completed successfully    prefect_task.py:45
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⠦ Completed Sorter By Metric ████████░░ 8/9 88.9% • 0:00:02 3.2/sec • 0s remaining
[Progress bar stays pinned, logs scroll above]
```

## Benefits of New Approach
- **Simpler architecture**: Use Rich's built-in coordination instead of fighting it
- **Single console source**: Progress.console handles all output coordination
- **Prefect 3.0 compatible**: Uses Prefect's configurable handler architecture
- **Minimal code changes**: Leverage existing Rich functionality
- **Future-proof**: Works with Prefect's progress artifacts system

## Migration from TerminalDisplayManager
- **Remove**: Complex Live Display manual management
- **Replace**: Use Progress.console for all logging coordination
- **Simplify**: Let Progress handle its own Live Display lifecycle
- **Intercept**: Replace Prefect's console handler with our RichHandler

## Original Implementation Steps (For Reference - Deprecated Approach)

### Step 1: Create TerminalDisplayManager
```python
# src/interface/cli/display_manager.py
class TerminalDisplayManager:
    """Single source of truth for all terminal output coordination."""
    def __init__(self):
        self._console = Console()
        self._live_display = None
        self._progress = None
        
    async def __aenter__(self):
        # Set up Live Display with stdout/stderr redirection
        self._progress = Progress(console=self._console)
        self._live_display = Live(
            self._progress, 
            console=self._console,
            refresh_per_second=10,
            redirect_stdout=True,
            redirect_stderr=True
        )
        self._live_display.start()
        return self
        
    async def __aexit__(self, *args):
        if self._live_display:
            self._live_display.stop()
```

### Step 2: Integrate Loguru with Live Console
```python
# src/config/logging.py - Update configure_live_display_logging()
def configure_live_display_logging(live_console: Console) -> None:
    """Route ALL logging through Live Display console."""
    # Remove existing handlers
    logger.remove()
    
    # Custom sink for Loguru
    def live_console_sink(message):
        live_console.print(str(message).rstrip(), highlight=False)
    
    logger.add(live_console_sink, level="INFO", colorize=True)
    
    # Capture Python logging (Prefect)
    root_logger = logging.getLogger()
    root_logger.handlers = [LiveDisplayHandler(live_console)]
```

### Step 3: Update RichProgressProvider  
```python
# src/interface/cli/progress_provider.py - Use TerminalDisplayManager
class RichProgressProvider:
    def __init__(self, display_manager: TerminalDisplayManager):
        self.display_manager = display_manager
        self._progress = display_manager.get_progress()
        
    async def start_display(self):
        # Display manager handles Live Display lifecycle
        configure_live_display_logging(self.display_manager.get_console())
```

### Step 4: Integration Points
- **Workflow integration**: `progress_integration.py` uses `TerminalDisplayManager` 
- **CLI commands**: All progress-enabled commands use the display manager
- **Logging coordination**: Both Loguru and Prefect logs route through Live Display console

## Expected Result
```
[Colorful application logs scroll above]
[More workflow logs...]
[Prefect task logs...]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⠦ Processing Workflow ████████░░ 80% • 2/5 tasks • 30s remaining
[Progress bar stays pinned at bottom]
```

## Benefits
- **Single coordination point** for all terminal output
- **Type-safe architecture** with protocols and proper DI
- **Future-ready** for WebSocket/SSE integration
- **Docker Compose-style UX** with pinned progress bars

## Technical Reference

### Rich Live Display Capabilities
1. **Live Updating Display**: `Live` class for real-time terminal updates with history retention
2. **Console Integration**: Live Display creates internal Console object accessible via `console` attribute
3. **Pinned Progress**: Progress bars stay fixed while other output scrolls above
4. **Refresh Control**: Customizable refresh rates (default 4/sec, configurable via `refresh_per_second`)
5. **Transient Mode**: Option to clear display when exiting (`transient=True`)

### Core Pattern (from Rich documentation)
```python
from rich.live import Live
from rich.progress import Progress
from rich.console import Console

console = Console()
progress = Progress(console=console)

with Live(progress, console=console, refresh_per_second=10) as live:
    task = progress.add_task("Workflow", total=9)
    
    # Application logs appear above progress bar
    console.print("Application logs scroll above")
    progress.update(task, advance=1)
```

### Loguru Integration Challenge
**Current Setup** (`src/config/logging.py`):
```python
# Loguru writes directly to sys.stdout
{
    "sink": sys.stdout,
    "level": console_level,
    "format": console_format,
    "colorize": True,
}
```

**Integration Challenge**: Loguru bypasses Rich Live Display terminal control

**Solution Pattern**:
```python
class RichLoguruHandler:
    """Route Loguru output through Rich console for proper display layering."""
    def __init__(self, rich_console: Console):
        self.console = rich_console
    
    def write(self, message: str):
        self.console.print(message, end="")
    
    def flush(self):
        pass  # Rich handles flushing
```

### Key Resources
- [Rich Live Display Documentation](https://rich.readthedocs.io/en/stable/live.html)
- [Rich Progress Display Documentation](https://rich.readthedocs.io/en/stable/progress.html)
- [Rich Console Documentation](https://rich.readthedocs.io/en/latest/reference/console.html)
- [Rich GitHub Examples](https://github.com/Textualize/rich/blob/master/examples/dynamic_progress.py)

## Technical Debt Cleanup

**Problem**: Multiple failed attempts at Rich Live Display integration have created inconsistent patterns and debugging code that needs cleanup.

### Files Requiring Audit & Cleanup

#### `src/config/logging.py`
**Issues to Fix:**
- **Multiple logging configuration functions**: `configure_rich_console_logging()`, `configure_live_display_logging()`, `restore_normal_logging()`
- **Inconsistent handler storage**: Different patterns for saving/restoring original handlers
- **Dead code**: Unused `RichLoguruHandler` class and lambda sinks
- **Complex Python logging integration**: Root logger manipulation that may conflict

**Cleanup Actions:**
- Remove deprecated `configure_rich_console_logging()` function
- Consolidate into single, clean `configure_live_display_logging()` function
- Remove experimental lambda sink approaches
- Clean up Python logging handler management

#### `src/interface/cli/progress_provider.py`
**Issues to Fix:**
- **Architectural confusion**: Mixed approaches with Progress/Live Display coordination
- **Multiple refresh strategies**: Manual `refresh()` calls, varying refresh rates
- **Inconsistent console usage**: Switching between `self._console` and `live_display.console`
- **Complex logging configuration**: Multiple approaches to Loguru integration

**Cleanup Actions:**
- Settle on single Progress/Live Display architecture pattern
- Remove experimental refresh coordination code
- Consolidate console management approach
- Remove debugging/experimental code paths

#### `src/interface/cli/console.py`
**Issues to Fix:**
- **Unused context managers**: `live_display_context()` that may not be used consistently
- **Dead configuration functions**: Loguru routing functions that duplicate `logging.py`
- **Complex command detection**: Logic that may be over-engineered

**Cleanup Actions:**
- Audit usage of `live_display_context()` - remove if unused
- Remove duplicate Loguru configuration functions
- Simplify command detection logic if over-complex

### Patterns to Standardize

#### Console Management
**Current Issues:**
- Multiple console instances created across files
- Inconsistent console sharing between Progress and Live Display
- Mixed approaches to console redirection

**Target Pattern:**
```python
# Single console instance managed by TerminalDisplayManager
# All progress providers use the same console instance
# Clear separation between console creation and usage
```

#### Logging Configuration
**Current Issues:**
- Multiple functions doing similar logging setup
- Complex handler save/restore logic
- Inconsistent error handling

**Target Pattern:**
```python
# Single function: configure_live_display_logging()
# Simple handler replacement without complex state management
# Clear error handling and fallback behavior
```

#### Progress Display Lifecycle
**Current Issues:**
- Multiple ways to start/stop progress display
- Complex coordination between Progress and Live Display
- Inconsistent cleanup patterns

**Target Pattern:**
```python
# TerminalDisplayManager owns entire lifecycle
# Progress providers register with manager, don't manage display directly
# Clean context manager pattern with guaranteed cleanup
```

### Cleanup Checklist
- [ ] Audit all console creation - consolidate to single source
- [ ] Remove deprecated logging configuration functions
- [ ] Clean up experimental Progress/Live Display coordination code
- [ ] Standardize error handling patterns across logging integration
- [ ] Remove unused imports and dead code from failed attempts
- [ ] Update any inconsistent patterns in CLI command files
- [ ] Test cleanup doesn't break existing functionality
- [ ] Verify type safety after cleanup (run `basedpyright src/`)

