import json
import logging
import os
import traceback
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status, Path
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Boolean, DateTime, select, delete as sql_delete, func, and_, or_, text, inspect
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Configuration
PROXY_API_KEY = os.getenv("PROXY_API_KEY")
PROXY_API_KEY_FILE = os.getenv("PROXY_API_KEY_FILE")
if PROXY_API_KEY_FILE and os.path.exists(PROXY_API_KEY_FILE):
    with open(PROXY_API_KEY_FILE, "r", encoding="utf-8") as f:
        PROXY_API_KEY = f.read().strip()
elif PROXY_API_KEY:
    PROXY_API_KEY = PROXY_API_KEY.strip()

PROXY_READONLY_API_KEY = os.getenv("PROXY_READONLY_API_KEY")
PROXY_READONLY_API_KEY_FILE = os.getenv("PROXY_READONLY_API_KEY_FILE")
if PROXY_READONLY_API_KEY_FILE and os.path.exists(PROXY_READONLY_API_KEY_FILE):
    with open(PROXY_READONLY_API_KEY_FILE, "r", encoding="utf-8") as f:
        PROXY_READONLY_API_KEY = f.read().strip()
elif PROXY_READONLY_API_KEY:
    PROXY_READONLY_API_KEY = PROXY_READONLY_API_KEY.strip()

DATABASE_URL = os.getenv("DATABASE_URL")
DB_USER = os.getenv("DB_USER", "restdb_user")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_NAME = os.getenv("DB_NAME", "restdb_proxy")

# Build database URL if not provided
if not DATABASE_URL:
    DATABASE_URL = f"mysql+aiomysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# SQLAlchemy setup
engine = create_async_engine(DATABASE_URL, echo=False, future=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()


# Models
class Queue(Base):
    __tablename__ = "queue"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=False)
    priority = Column(Boolean, default=False, nullable=False)
    title = Column(String(1024), nullable=True)
    thumbnailUrl = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class RequestModel(Base):
    __tablename__ = "requests"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=False)
    title = Column(String(1024), nullable=True)
    thumbnailUrl = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)


class Quote(Base):
    __tablename__ = "quote"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=False)
    liveId = Column(String(255), nullable=False)
    title = Column(String(1024), nullable=True)
    thumbnailUrl = Column(String(1024), nullable=True)
    quotedAt = Column(DateTime, default=func.now(), nullable=False)


class Config(Base):
    __tablename__ = "config"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(255), nullable=False, unique=True)
    value = Column(String(2048), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class NowPlaying(Base):
    __tablename__ = "nowplaying"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=True)
    title = Column(String(1024), nullable=True)
    duration = Column(Integer, nullable=True)
    remainingTime = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)


class VideoInfoCache(Base):
    __tablename__ = "video_info_cache"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=False, unique=True)
    title = Column(String(1024), nullable=True)
    thumbnailUrl = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)




# Pydantic models
class QueueItem(BaseModel):
    videoId: str
    priority: Optional[bool] = False


# FastAPI app
app = FastAPI(title="restdb.io compatibility proxy (MariaDB backend)")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def http_method_override_middleware(request: Request, call_next):
    """Support X-HTTP-Method-Override header to override HTTP methods."""
    method_override = request.headers.get("x-http-method-override")
    if method_override:
        method_override = method_override.upper()
        if method_override in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
            request.scope["method"] = method_override
            logger.info(f"Method overridden to {method_override} via X-HTTP-Method-Override")
            
    response = await call_next(request)
    return response


# Dependency: get database session
async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


# API key check
async def check_api_key(
    request: Request,
    x_apikey: Optional[str] = Header(None),
    apikey: Optional[str] = None
):
    # 1. マスターキー(PROXY_API_KEY)が一致すれば、すべてのメソッドを許可
    key = x_apikey or apikey
    if PROXY_API_KEY and key == PROXY_API_KEY:
        return
        
    # 2. 読み取り専用キー(PROXY_READONLY_API_KEY)が一致し、メソッドがGET/HEAD/OPTIONSの場合のみ許可
    if PROXY_READONLY_API_KEY and key == PROXY_READONLY_API_KEY:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Write operations are not allowed with a read-only API key"
            )
            
    # 3. どちらのキーも一致しない、もしくはキーが指定されていない場合 (セキュリティ強化)\n    if PROXY_API_KEY or PROXY_READONLY_API_KEY:\n        raise HTTPException(\n            status_code=status.HTTP_403_FORBIDDEN, \n            detail="Invalid or missing API key"\n        )


# Utility: Parse MongoDB-like query
def parse_mongodb_query(q_str: str) -> Dict[str, Any]:
    """Parse MongoDB-like query from JSON string."""
    try:
        return json.loads(q_str) if q_str else {}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse query: {e}")
        return {}


# Utility: Parse header options (h parameter)
def parse_header_options(h_str: str) -> Dict[str, Any]:
    """Parse header options: {$orderby, $fields, $max, $skip}"""
    try:
        return json.loads(h_str) if h_str else {}
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse header options: {e}")
        return {}


# Utility: Build ORM filter from MongoDB query
def build_filter(query: Dict[str, Any], model) -> Any:
    """Convert MongoDB-like query to SQLAlchemy filter."""
    filters = []
    for field_name, condition in query.items():
        # Handle logical operators
        if field_name == "$or" and isinstance(condition, list):
            or_filters = []
            for sub_query in condition:
                sub_filter = build_filter(sub_query, model)
                if sub_filter is not None:
                    or_filters.append(sub_filter)
            if or_filters:
                filters.append(or_(*or_filters))
            continue
            
        if field_name == "$and" and isinstance(condition, list):
            and_filters = []
            for sub_query in condition:
                sub_filter = build_filter(sub_query, model)
                if sub_filter is not None:
                    and_filters.append(sub_filter)
            if and_filters:
                filters.append(and_(*and_filters))
            continue

        # Map _id to id (restdb.io compatibility)
        mapped_field_name = "id" if field_name == "_id" else field_name
        field = getattr(model, mapped_field_name, None)
        if field is None:
            continue
        
        if isinstance(condition, dict):
            # MongoDB operators
            if "$eq" in condition:
                filters.append(field == condition["$eq"])
            if "$gt" in condition:
                filters.append(field > condition["$gt"])
            if "$lt" in condition:
                filters.append(field < condition["$lt"])
            if "$gte" in condition:
                filters.append(field >= condition["$gte"])
            if "$lte" in condition:
                filters.append(field <= condition["$lte"])
            if "$in" in condition:
                filters.append(field.in_(condition["$in"]))
            if "$nin" in condition:
                filters.append(~field.in_(condition["$nin"]))
            if "$ne" in condition:
                filters.append(field != condition["$ne"])
            if "$regex" in condition:
                # MariaDB's regular expression search via SQLAlchemy regexp_match
                filters.append(field.regexp_match(condition["$regex"]))
        else:
            # Direct equality
            filters.append(field == condition)
    
    return and_(*filters) if filters else None


# Events
MAX_DB_INIT_RETRIES = int(os.getenv("DB_INIT_RETRIES", "10"))
DB_INIT_RETRY_DELAY = float(os.getenv("DB_INIT_RETRY_DELAY", "3"))

async def migrate_database(conn):
    """queue, requests, quote テーブルに title と thumbnailUrl カラムがあるか確認し、なければ追加する"""
    def check_and_add_columns(connection):
        inspector = inspect(connection)
        for table_name in ["queue", "requests", "quote"]:
            if not inspector.has_table(table_name):
                continue
            
            columns = [col["name"] for col in inspector.get_columns(table_name)]
            
            if "title" not in columns:
                logger.info(f"Adding column 'title' to table '{table_name}'")
                connection.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `title` VARCHAR(1024) NULL"))
                
            if "thumbnailUrl" not in columns:
                logger.info(f"Adding column 'thumbnailUrl' to table '{table_name}'")
                connection.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `thumbnailUrl` VARCHAR(1024) NULL"))

    try:
        await conn.run_sync(check_and_add_columns)
    except Exception as e:
        logger.warning(f"Failed to migrate tables: {e}")

@app.on_event("startup")
async def startup_event():
    """Create tables on startup, retrying until the database is ready."""
    logger.info(f"Attempting to connect to database at {DB_HOST}:{DB_PORT}/{DB_NAME}")
    for attempt in range(1, MAX_DB_INIT_RETRIES + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                # 自動マイグレーションの実行
                await migrate_database(conn)
            logger.info("Database tables created/verified/migrated successfully")
            return
        except Exception as e:
            logger.warning(
                f"Database initialization attempt {attempt}/{MAX_DB_INIT_RETRIES} failed: {e}"
            )
            logger.debug(f"Startup error details:\n{traceback.format_exc()}")
            if attempt == MAX_DB_INIT_RETRIES:
                logger.error(
                    "Database initialization failed after maximum retries. Aborting startup."
                )
                raise
            await asyncio.sleep(DB_INIT_RETRY_DELAY)


# Health check
@app.get("/health")
async def health_check(session: AsyncSession = Depends(get_session)):
    """Health check endpoint - no API key required."""
    try:
        await session.execute(select(1))
        return {"status": "ok", "database": "connected"}
    except Exception:
        logger.exception("Health check failed")
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={"status": "error", "database": "failed", "error": "Internal server error"}
        )


# ============================================================
# Generic REST API endpoints for collections (queue, requests)
# ============================================================

def get_model_by_collection(collection: str):
    """Return the model class for a collection name dynamically from SQLAlchemy models."""
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if getattr(cls, "__tablename__", None) == collection:
            return cls
    return None


@app.get("/rest/{collection}")
@app.get("/{collection}")
async def get_collection(
    collection: str = Path(..., pattern="^[a-zA-Z0-9_-]+$"),
    q: Optional[str] = None,
    h: Optional[str] = None,
    sort: Optional[str] = None,
    dir: Optional[int] = None,
    skip: Optional[int] = None,
    max: Optional[int] = None,
    metafields: Optional[bool] = None,
    totals: Optional[bool] = None,
    count: Optional[bool] = None,
    apikey: Optional[str] = None,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """GET /rest/<collection> - Get list with optional MongoDB query and header options."""
    try:
        # 予約語の除外チェック
        if collection in {"health", "docs", "redoc", "openapi.json", "rest", "_meta"}:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")

        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        # Parse query and options
        query_obj = parse_mongodb_query(q)
        options_obj = parse_header_options(h)
        
        # Merge individual query parameters into options_obj
        if sort is not None:
            direction = dir if dir is not None else 1
            options_obj["$orderby"] = {sort: direction}
        elif dir is not None and "$orderby" in options_obj:
            for k in options_obj["$orderby"]:
                options_obj["$orderby"][k] = dir
                
        if skip is not None:
            options_obj["$skip"] = skip
            
        if max is not None:
            options_obj["$max"] = max
            
        if metafields is not None:
            options_obj["$metafields"] = metafields
            
        is_totals = totals or False
        is_count = count or False
        
        logger.debug(f"GET /rest/{collection}: q={query_obj}, h={options_obj}")
        
        # Determine total count if totals option is enabled
        total_count = 0
        if is_totals:
            count_stmt = select(func.count()).select_from(model)
            if query_obj:
                filter_clause = build_filter(query_obj, model)
                if filter_clause is not None:
                    count_stmt = count_stmt.where(filter_clause)
            count_result = await session.execute(count_stmt)
            total_count = count_result.scalar() or 0
            
            # If only count is requested, return early
            if is_count:
                return {
                    "data": [],
                    "totals": {
                        "count": total_count
                    }
                }
        
        # Build base query
        stmt = select(model)
        
        # Apply filters
        if query_obj:
            filter_clause = build_filter(query_obj, model)
            if filter_clause is not None:
                stmt = stmt.where(filter_clause)
        
        # Apply ordering (from $orderby in options)
        orderby = options_obj.get("$orderby", {})
        for field_name, direction in orderby.items():
            # Map _id to id (restdb.io compatibility)
            mapped_field_name = "id" if field_name == "_id" else field_name
            field = getattr(model, mapped_field_name, None)
            if field:
                if direction == -1:
                    stmt = stmt.order_by(field.desc())
                else:
                    stmt = stmt.order_by(field.asc())
        
        # Apply paging
        skip_val = options_obj.get("$skip", 0)
        limit_val = options_obj.get("$max", 1000) # Default to 1000 records
        if skip_val:
            stmt = stmt.offset(skip_val)
        if limit_val and limit_val > 0:
            stmt = stmt.limit(limit_val)
        
        result = await session.execute(stmt)
        rows = result.scalars().all()
        
        # Build response with $fields filtering
        fields = options_obj.get("$fields", {})
        # Map _id in fields to id
        if "_id" in fields:
            fields["id"] = fields.pop("_id")
            
        include_metafields = options_obj.get("$metafields", False)
        
        items = []
        for row in rows:
            obj = {"_id": str(row.id)}
            for col in row.__table__.columns:
                if col.name != "id":
                    if not fields or col.name in fields:
                        obj[col.name] = getattr(row, col.name)
            
            if include_metafields:
                created_val = getattr(row, "created_at", None) or getattr(row, "quotedAt", None)
                if created_val and isinstance(created_val, datetime):
                    obj["_created"] = created_val.isoformat() + "Z"
                
                changed_val = getattr(row, "updated_at", None) or created_val
                if changed_val and isinstance(changed_val, datetime):
                    obj["_changed"] = changed_val.isoformat() + "Z"
                    
                obj["_version"] = 0
                
            items.append(obj)
            
        if is_totals:
            return {
                "data": items,
                "totals": {
                    "total": total_count,
                    "count": len(items),
                    "skip": skip_val,
                    "max": limit_val
                }
            }
        
        return items
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in GET /rest/{collection}: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.get("/rest/{collection}/{item_id}")
@app.get("/{collection}/{item_id}")
async def get_collection_item(
    item_id: str,
    collection: str = Path(..., pattern="^[a-zA-Z0-9_-]+$"),
    metafields: Optional[bool] = None,
    apikey: Optional[str] = None,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """GET /rest/<collection>/ID - Get single document by ID."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        if not item_id.isdigit():
            raise HTTPException(status_code=400, detail=f"Invalid item ID: {item_id}. ID must be an integer.")
        
        stmt = select(model).where(model.id == int(item_id))
        result = await session.execute(stmt)
        row = result.scalars().first()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Document not found: {item_id}")
        
        obj = {"_id": str(row.id)}
        for col in row.__table__.columns:
            if col.name != "id":
                obj[col.name] = getattr(row, col.name)
                
        if metafields:
            created_val = getattr(row, "created_at", None) or getattr(row, "quotedAt", None)
            if created_val and isinstance(created_val, datetime):
                obj["_created"] = created_val.isoformat() + "Z"
            
            changed_val = getattr(row, "updated_at", None) or created_val
            if changed_val and isinstance(changed_val, datetime):
                obj["_changed"] = changed_val.isoformat() + "Z"
                
            obj["_version"] = 0
            
        return obj
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in GET /rest/{collection}/{item_id}: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.post("/rest/{collection}", status_code=201)
@app.post("/{collection}", status_code=201)
async def post_collection(
    request: Request,
    collection: str = Path(..., pattern="^[a-zA-Z0-9_-]+$"),
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """POST /rest/<collection> - Create one or more documents."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        body = await request.json()
        if isinstance(body, list):
            items = body
        else:
            items = [body]
        
        db_items = []
        for item in items:
            # Filter only valid columns
            kwargs = {}
            for col in model.__table__.columns:
                if col.name != "id" and col.name in item:
                    val = item[col.name]
                    if isinstance(col.type, DateTime) and isinstance(val, str):
                        try:
                            # Python 3.11以降なら Z や +00:00 を解釈可能
                            dt = datetime.fromisoformat(val.replace('Z', '+00:00'))
                            val = dt.replace(tzinfo=None)
                        except ValueError:
                            # パースに失敗した場合はそのままにする（あるいはエラーを返す処理にする）
                            pass
                    kwargs[col.name] = val
            db_item = model(**kwargs)
            session.add(db_item)
            db_items.append(db_item)
        
        await session.commit()
        for db_item in db_items:
            await session.refresh(db_item)
        
        # Return created items with IDs
        created = []
        for db_item in db_items:
            obj = {"_id": str(db_item.id)}
            for col in db_item.__table__.columns:
                if col.name != "id":
                    obj[col.name] = getattr(db_item, col.name)
            
            # Add metafields by default for newly created items
            created_val = getattr(db_item, "created_at", None) or getattr(db_item, "quotedAt", None)
            if created_val and isinstance(created_val, datetime):
                obj["_created"] = created_val.isoformat() + "Z"
            
            changed_val = getattr(db_item, "updated_at", None) or created_val
            if changed_val and isinstance(changed_val, datetime):
                obj["_changed"] = changed_val.isoformat() + "Z"
                
            obj["_version"] = 0
            
            created.append(obj)
        
        if isinstance(body, list):
            return created
        else:
            return created[0] if created else {}
    except IntegrityError as e:
        logger.warning(f"Integrity error in POST /rest/{collection}: {e}")
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Database integrity error (e.g., missing required field or duplicate): {e.orig}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in POST /rest/{collection}: {e}\n{traceback.format_exc()}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.put("/rest/{collection}/{item_id}")
@app.put("/{collection}/{item_id}")
async def put_collection_item(
    item_id: str,
    request: Request,
    collection: str = Path(..., pattern="^[a-zA-Z0-9_-]+$"),
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """PUT /rest/<collection>/ID - Update (replace) entire document."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        if not item_id.isdigit():
            raise HTTPException(status_code=400, detail=f"Invalid item ID: {item_id}. ID must be an integer.")
        
        body = await request.json()
        
        stmt = select(model).where(model.id == int(item_id))
        result = await session.execute(stmt)
        db_item = result.scalars().first()
        
        if not db_item:
            raise HTTPException(status_code=404, detail=f"Document not found: {item_id}")
        
        # Update all fields
        for col in model.__table__.columns:
            if col.name != "id" and col.name in body:
                setattr(db_item, col.name, body[col.name])
        
        await session.commit()
        await session.refresh(db_item)
        
        obj = {"_id": str(db_item.id)}
        for col in db_item.__table__.columns:
            if col.name != "id":
                obj[col.name] = getattr(db_item, col.name)
                
        # Add metafields
        created_val = getattr(db_item, "created_at", None) or getattr(db_item, "quotedAt", None)
        if created_val and isinstance(created_val, datetime):
            obj["_created"] = created_val.isoformat() + "Z"
        
        changed_val = getattr(db_item, "updated_at", None) or created_val
        if changed_val and isinstance(changed_val, datetime):
            obj["_changed"] = changed_val.isoformat() + "Z"
            
        obj["_version"] = 0
        
        return obj
    except IntegrityError as e:
        logger.warning(f"Integrity error in PUT /rest/{collection}/{item_id}: {e}")
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Database integrity error: {e.orig}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in PUT /rest/{collection}/{item_id}: {e}\n{traceback.format_exc()}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.patch("/rest/{collection}/{item_id}")
@app.patch("/{collection}/{item_id}")
async def patch_collection_item(
    item_id: str,
    request: Request,
    collection: str = Path(..., pattern="^[a-zA-Z0-9_-]+$"),
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """PATCH /rest/<collection>/ID - Partial update."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        if not item_id.isdigit():
            raise HTTPException(status_code=400, detail=f"Invalid item ID: {item_id}. ID must be an integer.")
        
        body = await request.json()
        
        stmt = select(model).where(model.id == int(item_id))
        result = await session.execute(stmt)
        db_item = result.scalars().first()
        
        if not db_item:
            raise HTTPException(status_code=404, detail=f"Document not found: {item_id}")
        
        # Update only provided fields
        columns = db_item.__table__.columns.keys()
        for key, value in body.items():
            if key in columns and key != "id":
                setattr(db_item, key, value)
        
        await session.commit()
        await session.refresh(db_item)
        
        obj = {"_id": str(db_item.id)}
        for col in db_item.__table__.columns:
            if col.name != "id":
                obj[col.name] = getattr(db_item, col.name)
                
        # Add metafields
        created_val = getattr(db_item, "created_at", None) or getattr(db_item, "quotedAt", None)
        if created_val and isinstance(created_val, datetime):
            obj["_created"] = created_val.isoformat() + "Z"
        
        changed_val = getattr(db_item, "updated_at", None) or created_val
        if changed_val and isinstance(changed_val, datetime):
            obj["_changed"] = changed_val.isoformat() + "Z"
            
        obj["_version"] = 0
        
        return obj
    except IntegrityError as e:
        logger.warning(f"Integrity error in PATCH /rest/{collection}/{item_id}: {e}")
        await session.rollback()
        raise HTTPException(status_code=400, detail=f"Database integrity error: {e.orig}")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in PATCH /rest/{collection}/{item_id}: {e}\n{traceback.format_exc()}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.delete("/rest/{collection}/*")
@app.delete("/{collection}/*")
async def delete_collection_bulk(
    collection: str = Path(..., pattern="^[a-zA-Z0-9_-]+$"),
    q: Optional[str] = None,
    request: Request = None,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /rest/<collection>/* - Delete multiple documents (by IDs or query)."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        deleted_count = 0
        
        # Delete by query
        if q:
            query_obj = parse_mongodb_query(q)
            stmt = select(model)
            if query_obj:
                filter_clause = build_filter(query_obj, model)
                if filter_clause is not None:
                    stmt = stmt.where(filter_clause)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            for row in rows:
                await session.delete(row)
            deleted_count = len(rows)

        # Delete by ID list in body
        else:
            try:
                body_bytes = await request.body()
                if not body_bytes:
                    raise HTTPException(
                        status_code=400, 
                        detail="Delete body is empty. To delete all items, a query must be provided."
                    )
                
                body = json.loads(body_bytes)
                if isinstance(body, list):
                    valid_ids = []
                    for item_id in body:
                        try:
                            valid_ids.append(int(item_id))
                        except ValueError:
                            raise HTTPException(
                                status_code=400, 
                                detail=f"Invalid ID format in list: {item_id}"
                            )
                    
                    if valid_ids:
                        stmt = sql_delete(model).where(model.id.in_(valid_ids))
                        result = await session.execute(stmt)
                        deleted_count = result.rowcount
                else:
                    raise HTTPException(
                        status_code=400, 
                        detail="Request body must be a list of IDs for bulk delete."
                    )
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=400, 
                    detail="Invalid JSON in request body."
                )
        
        await session.commit()
        return {"deleted": deleted_count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in DELETE /rest/{collection}/*: {e}\n{traceback.format_exc()}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.delete("/rest/{collection}/{item_id}")
@app.delete("/{collection}/{item_id}")
async def delete_collection_item(
    item_id: str,
    collection: str = Path(..., pattern="^[a-zA-Z0-9_-]+$"),
    q: Optional[str] = None,
    request: Request = None,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /rest/<collection>/ID - Delete single document."""
    try:
        if item_id == "*":
            return await delete_collection_bulk(collection, q=q, request=request, _=_, session=session)

        if not item_id.isdigit():
            raise HTTPException(status_code=400, detail=f"Invalid item ID: {item_id}. ID must be an integer.")

        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        stmt = sql_delete(model).where(model.id == int(item_id))
        await session.execute(stmt)
        await session.commit()
        return {"deleted": item_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in DELETE /rest/{collection}/{item_id}: {e}\n{traceback.format_exc()}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


# Meta Data API
@app.get("/rest/_meta")
async def get_meta(_: Any = Depends(check_api_key)):
    """GET /rest/_meta - Get database metadata."""
    try:
        return {
            "collections": ["queue", "requests", "quote", "config", "nowplaying", "video_info_cache"],
            "version": "1.0",
            "backend": "MariaDB",
        }
    except Exception as e:
        logger.error(f"Error in GET /rest/_meta: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/rest/{collection}/_meta")
async def get_collection_meta(
    collection: str,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """GET /rest/<collection>/_meta - Get collection metadata."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        # Count documents
        stmt = select(func.count()).select_from(model)
        result = await session.execute(stmt)
        count = result.scalar()
        
        # Get field information
        fields = []
        for col in model.__table__.columns:
            fields.append({
                "name": col.name,
                "type": str(col.type),
                "nullable": col.nullable,
                "primary_key": col.primary_key,
            })
        
        return {
            "collection": collection,
            "count": count,
            "fields": fields,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in GET /rest/{collection}/_meta: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")



