import json
import logging
import os
import traceback
import asyncio
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status, Path
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Boolean, DateTime, select, delete as sql_delete, func, and_, text
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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RequestModel(Base):
    __tablename__ = "requests"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=False)
    title = Column(String(1024), nullable=True)
    thumbnailUrl = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Quote(Base):
    __tablename__ = "quote"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=False)
    liveId = Column(String(255), nullable=False)
    title = Column(String(1024), nullable=True)
    thumbnailUrl = Column(String(1024), nullable=True)
    quotedAt = Column(DateTime, default=datetime.utcnow, nullable=False)


class Config(Base):
    __tablename__ = "config"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(255), nullable=False, unique=True)
    value = Column(String(2048), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class NowPlaying(Base):
    __tablename__ = "nowplaying"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=True)
    title = Column(String(1024), nullable=True)
    duration = Column(Integer, nullable=True)
    remainingTime = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class VideoInfoCache(Base):
    __tablename__ = "video_info_cache"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=False, unique=True)
    title = Column(String(1024), nullable=True)
    thumbnailUrl = Column(String(1024), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)




# Pydantic models
class QueueItem(BaseModel):
    videoId: str
    priority: Optional[bool] = False


# FastAPI app
app = FastAPI(title="restdb.io compatibility proxy (MariaDB backend)")


# Dependency: get database session
async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session


# API key check
async def check_api_key(request: Request, x_apikey: Optional[str] = Header(None)):
    # 1. マスターキー(PROXY_API_KEY)が一致すれば、すべてのメソッドを許可
    if PROXY_API_KEY and x_apikey == PROXY_API_KEY:
        return
        
    # 2. 読み取り専用キー(PROXY_READONLY_API_KEY)が一致し、メソッドがGET/HEAD/OPTIONSの場合のみ許可
    if PROXY_READONLY_API_KEY and x_apikey == PROXY_READONLY_API_KEY:
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Write operations are not allowed with a read-only API key"
            )
            
    # 3. どちらのキーも設定されていないか、不一致の場合
    if PROXY_API_KEY or PROXY_READONLY_API_KEY:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")


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
        else:
            # Direct equality
            filters.append(field == condition)
    
    return and_(*filters) if filters else None


# Events
MAX_DB_INIT_RETRIES = int(os.getenv("DB_INIT_RETRIES", "10"))
DB_INIT_RETRY_DELAY = float(os.getenv("DB_INIT_RETRY_DELAY", "3"))

async def migrate_database(conn):
    """queue, requests, quote テーブルに title と thumbnailUrl カラムがあるか確認し、なければ追加する"""
    for table_name in ["queue", "requests", "quote"]:
        try:
            # SHOW COLUMNS は MySQL/MariaDB 独自構文
            result = await conn.execute(text(f"SHOW COLUMNS FROM `{table_name}`"))
            columns = [row[0] for row in result.fetchall()]
            
            if "title" not in columns:
                logger.info(f"Adding column 'title' to table '{table_name}'")
                await conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `title` VARCHAR(1024) NULL"))
                
            if "thumbnailUrl" not in columns:
                logger.info(f"Adding column 'thumbnailUrl' to table '{table_name}'")
                await conn.execute(text(f"ALTER TABLE `{table_name}` ADD COLUMN `thumbnailUrl` VARCHAR(1024) NULL"))
        except Exception as e:
            logger.warning(f"Failed to migrate table '{table_name}': {e}")

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
    collection: str = Path(..., pattern="^(?!health|docs|redoc|openapi\\.json|rest|_meta)[a-zA-Z0-9_-]+$"),
    q: Optional[str] = None,
    h: Optional[str] = None,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """GET /rest/<collection> - Get list with optional MongoDB query and header options."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        # Parse query and options
        query_obj = parse_mongodb_query(q)
        options_obj = parse_header_options(h)
        
        logger.debug(f"GET /rest/{collection}: q={query_obj}, h={options_obj}")
        
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
        skip = options_obj.get("$skip", 0)
        limit = options_obj.get("$max", None)
        if skip:
            stmt = stmt.offset(skip)
        if limit:
            stmt = stmt.limit(limit)
        
        result = await session.execute(stmt)
        rows = result.scalars().all()
        
        # Build response with $fields filtering
        fields = options_obj.get("$fields", {})
        # Map _id in fields to id
        if "_id" in fields:
            fields["id"] = fields.pop("_id")
        
        items = []
        for row in rows:
            obj = {"_id": str(row.id)}
            for col in row.__table__.columns:
                if col.name != "id":
                    if not fields or col.name in fields:
                        obj[col.name] = getattr(row, col.name)
            items.append(obj)
        
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
    collection: str = Path(..., pattern="^(?!health|docs|redoc|openapi\\.json|rest|_meta)[a-zA-Z0-9_-]+$"),
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """GET /rest/<collection>/ID - Get single document by ID."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        stmt = select(model).where(model.id == int(item_id))
        result = await session.execute(stmt)
        row = result.scalars().first()
        
        if not row:
            raise HTTPException(status_code=404, detail=f"Document not found: {item_id}")
        
        obj = {"_id": str(row.id)}
        for col in row.__table__.columns:
            if col.name != "id":
                obj[col.name] = getattr(row, col.name)
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
    collection: str = Path(..., pattern="^(?!health|docs|redoc|openapi\\.json|rest|_meta)[a-zA-Z0-9_-]+$"),
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
        
        created = []
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
        
        await session.commit()
        
        # Return created items with IDs
        for item in items:
            # Query to get the last inserted row
            latest_stmt = select(model).order_by(model.id.desc()).limit(1)
            result = await session.execute(latest_stmt)
            db_item = result.scalars().first()
            if db_item:
                obj = {"_id": str(db_item.id)}
                for col in db_item.__table__.columns:
                    if col.name != "id":
                        obj[col.name] = getattr(db_item, col.name)
                created.append(obj)
        
        return created
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
    collection: str = Path(..., pattern="^(?!health|docs|redoc|openapi\\.json|rest|_meta)[a-zA-Z0-9_-]+$"),
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """PUT /rest/<collection>/ID - Update (replace) entire document."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
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
        
        obj = {"_id": str(db_item.id)}
        for col in db_item.__table__.columns:
            if col.name != "id":
                obj[col.name] = getattr(db_item, col.name)
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
    collection: str = Path(..., pattern="^(?!health|docs|redoc|openapi\\.json|rest|_meta)[a-zA-Z0-9_-]+$"),
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """PATCH /rest/<collection>/ID - Partial update."""
    try:
        model = get_model_by_collection(collection)
        if not model:
            raise HTTPException(status_code=404, detail=f"Collection '{collection}' not found")
        
        body = await request.json()
        
        stmt = select(model).where(model.id == int(item_id))
        result = await session.execute(stmt)
        db_item = result.scalars().first()
        
        if not db_item:
            raise HTTPException(status_code=404, detail=f"Document not found: {item_id}")
        
        # Update only provided fields
        for key, value in body.items():
            if hasattr(db_item, key) and key != "id":
                setattr(db_item, key, value)
        
        await session.commit()
        
        obj = {"_id": str(db_item.id)}
        for col in db_item.__table__.columns:
            if col.name != "id":
                obj[col.name] = getattr(db_item, col.name)
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
    collection: str = Path(..., pattern="^(?!health|docs|redoc|openapi\\.json|rest|_meta)[a-zA-Z0-9_-]+$"),
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
                body = await request.json()
                if isinstance(body, list):
                    for item_id in body:
                        stmt = sql_delete(model).where(model.id == int(item_id))
                        result = await session.execute(stmt)
                        deleted_count += result.rowcount
            except Exception:
                # Body is empty or invalid JSON -> Delete all documents
                stmt = sql_delete(model)
                result = await session.execute(stmt)
                deleted_count = result.rowcount
        
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
    collection: str = Path(..., pattern="^(?!health|docs|redoc|openapi\\.json|rest|_meta)[a-zA-Z0-9_-]+$"),
    q: Optional[str] = None,
    request: Request = None,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /rest/<collection>/ID - Delete single document."""
    try:
        if item_id == "*":
            return await delete_collection_bulk(collection, q=q, request=request, _=_, session=session)

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



