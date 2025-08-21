# 🎯 Active Work Tracker - Hard Delete Architecture Migration & Type Safety

> [!info] Purpose
> This file tracks the comprehensive migration from soft delete to hard delete architecture with complete type safety cleanup.

## 🎯 Hard Delete Architecture Migration

> [!abstract] **Initiative Overview**
> **Current Initiative**: Complete Hard Delete Architecture Migration with Type Safety Cleanup  
> **Status**: `#in-progress` `#architecture-migration` `#v2.2`  
> **Last Updated**: 2025-08-17

### Initiative Goal
Complete the architectural simplification from soft delete to hard delete everywhere, eliminating complexity while maintaining data integrity through proper cascade configurations and type safety.

**What We're Building:**
- **Clean Architecture Boundaries**: Domain entities separate from database models with `DB` prefixes
- **Hard Delete Everywhere**: Database-level CASCADE operations with SQLAlchemy 2.0 best practices
- **Type Safety**: Zero basedpyright errors with proper import structure
- **Performance**: Eliminate soft delete query overhead and filtering complexity

**Non-Negotiable Requirements:**
- **Clean Breaks**: No backward compatibility layers or legacy adapters
- **Type Safety**: Complete basedpyright compliance across entire codebase
- **Architecture Compliance**: Strict DDD + Hexagonal boundaries maintained
- **Cascade Safety**: Proper foreign key CASCADE configuration per SQLAlchemy 2.0 best practices

---

## 🏗️ SQLAlchemy 2.0 Hard Delete Best Practices (Research Complete)

### Key Configuration Principles
Based on official SQLAlchemy 2.0 documentation and community best practices:

**1. Database-Level Cascades (Recommended)**
```python
# Child model with CASCADE foreign key
parent_id: Mapped[int] = mapped_column(ForeignKey("parent.id", ondelete="CASCADE"))

# Parent model with passive_deletes=True  
children: Mapped[list["Child"]] = relationship(
    "Child",
    cascade="all, delete",
    passive_deletes=True  # Critical for performance
)
```

**2. Why `passive_deletes=True` is Essential**
- Without it, SQLAlchemy sets foreign keys to NULL before delete (breaks CASCADE)
- Database-level CASCADE is much more efficient than ORM individual deletes
- Prevents mixing ORM and database cascade mechanisms
- Allows database to chain cascade operations in single DELETE statement

**3. Performance Benefits**
- Database CASCADE eliminates N+1 delete queries
- Single DELETE can cascade across multiple relationship levels
- No need for ORM to individually load related collections
- Optimal for bulk operations and data cleanup

### Our Implementation Strategy

**Applied to Narada Models:**
- All `track_id` foreign keys use `ondelete="CASCADE"`
- All `playlist_id` foreign keys use `ondelete="CASCADE"`  
- Parent relationships use `passive_deletes=True`
- Remove all soft delete filtering throughout codebase

---

## ✅ Completed Foundation Work

### Database Architecture Simplification ✅
- **✅ Model Renaming**: All database models use `DB` prefix (DBTrack, DBPlaylist, etc.)
- **✅ Clean Architecture**: Domain entities (Track) separate from database models (DBTrack)
- **✅ Relationship Updates**: All SQLAlchemy relationships use correct `DB` prefixed names
- **✅ Import Structure**: Database layer imports fixed, no naming conflicts

### Type Safety Progress ✅
- **✅ 50% Error Reduction**: Reduced from 52 to 26 basedpyright errors
- **✅ Import Conflicts**: Resolved domain vs database model naming conflicts  
- **✅ Database Layer**: Infrastructure persistence imports corrected
- **✅ Repository Base**: Removed obsolete `filter_active` references

**Files Modified:**
- ✅ `src/infrastructure/persistence/database/db_models.py` - All models renamed with DB prefix
- ✅ `src/infrastructure/persistence/database/__init__.py` - Import statements updated
- ✅ `src/infrastructure/persistence/database/db_connection.py` - Model references fixed
- ✅ `src/infrastructure/persistence/repositories/__init__.py` - Removed filter_active
- ✅ `src/infrastructure/persistence/repositories/track/` - Core import fixes applied

---

## 📋 Current Sprint: Complete Soft Delete Removal

> [!todo] **Active Work: Remove All Soft Delete Patterns**
> Systematic removal of `is_deleted` references and conversion to hard delete patterns

### 🚨 Remaining Type Errors: 26 Total

**Service Layer (8 errors):**
- `src/application/services/track_merge_service.py` - Soft delete patterns in merge operations

**Repository Layer (18 errors):**  
- `src/infrastructure/persistence/repositories/playlist/core.py` - 8 soft delete references
- `src/infrastructure/persistence/repositories/playlist/mapper.py` - Active record filtering  
- Various track repositories - Remaining `is_deleted` filters

### 🔧 Current Phase: Systematic Soft Delete Cleanup

**Phase A: Repository Layer Cleanup** 🔄 *In Progress*
- [ ] **Remove `is_deleted` filters** from all query methods
- [ ] **Convert soft delete operations** to hard delete operations
- [ ] **Update filtering logic** - remove "active record" assumptions
- [ ] **Fix relationship loading** - no soft delete checks in mappers

**Phase B: Service Layer Migration**  
- [ ] **Track Merge Service** - Convert soft delete patterns to hard deletes
- [ ] **Update merge logic** - Remove `is_deleted` references  
- [ ] **Preserve functionality** - Same conflict resolution without soft deletes

**Phase C: CASCADE Configuration**
- [ ] **Add foreign key CASCADE** to all models per SQLAlchemy 2.0 best practices
- [ ] **Update relationships** with `passive_deletes=True` 
- [ ] **Test cascade behavior** - Verify proper deletion chains
- [ ] **Performance validation** - Confirm improved delete performance

### 🎯 Target Architecture

**Database Models with Proper CASCADE:**
```python
class DBTrack(BaseEntity):
    # Relationships with CASCADE support
    metrics: Mapped[list["DBTrackMetric"]] = relationship(
        "DBTrackMetric",
        cascade="all, delete", 
        passive_deletes=True
    )

class DBTrackMetric(BaseEntity):  
    # Foreign key with CASCADE
    track_id: Mapped[int] = mapped_column(
        ForeignKey("tracks.id", ondelete="CASCADE")
    )
```

**Repository Methods Without Soft Delete:**
```python
# BEFORE (soft delete pattern)
async def get_active_tracks(self):
    return await self.find_by([
        self.model_class.is_deleted == False  # ❌ Remove
    ])

# AFTER (hard delete pattern)  
async def get_tracks(self):
    return await self.find_by([])  # ✅ All records are active
```

---

## 🎯 Success Criteria

**Must Achieve:**
1. **Zero Type Errors**: `poetry run basedpyright src/` returns 0 errors
2. **Clean Architecture**: Domain entities clearly separated from database models  
3. **CASCADE Configured**: All foreign keys use proper `ondelete="CASCADE"`
4. **Performance Improved**: No soft delete query overhead
5. **Tests Passing**: All existing functionality preserved

**Validation Commands:**
```bash
poetry run basedpyright src/          # 0 errors
poetry run ruff check .               # Clean lint
poetry run pytest tests/              # All tests pass
```

---

## 🛠️ Implementation Details

### Key Files Requiring Updates

**Repository Layer (18 errors):**
- `src/infrastructure/persistence/repositories/playlist/core.py` - Remove is_deleted filters
- `src/infrastructure/persistence/repositories/playlist/mapper.py` - Remove active filtering  
- `src/infrastructure/persistence/repositories/track/plays.py` - Clean query methods
- `src/infrastructure/persistence/repositories/track/mapper.py` - Remove active checks

**Service Layer (8 errors):**
- `src/application/services/track_merge_service.py` - Convert to hard delete patterns

**Database Models:**
- `src/infrastructure/persistence/database/db_models.py` - Add CASCADE configurations

### CASCADE Implementation Strategy

**1. Update Foreign Keys:**
```python
# Add ondelete="CASCADE" to all foreign key relationships
track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id", ondelete="CASCADE"))
playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id", ondelete="CASCADE"))
```

**2. Update Relationships:**
```python
# Add passive_deletes=True to parent relationships
tracks: Mapped[list["DBTrack"]] = relationship(
    "DBTrack", 
    cascade="all, delete",
    passive_deletes=True
)
```

**3. Test Cascade Behavior:**
- Verify child records deleted when parent deleted
- Confirm no orphaned records remain
- Validate performance improvement over manual deletion

---

## 🚀 Next Steps

### Immediate Actions (Current Session)
1. **Continue Repository Cleanup** - Remove remaining `is_deleted` filters systematically
2. **Service Layer Migration** - Fix track merge service soft delete patterns  
3. **Type Safety Verification** - Run basedpyright after each fix
4. **CASCADE Configuration** - Add proper foreign key CASCADE settings

### Validation Strategy
- **Incremental Testing** - Run basedpyright after each file fix
- **Functionality Preservation** - Ensure existing operations still work
- **Performance Monitoring** - Validate improved delete performance
- **Architecture Compliance** - Maintain DDD + Hexagonal boundaries

**Target Completion**: End of current session with 0 type errors and complete hard delete architecture.