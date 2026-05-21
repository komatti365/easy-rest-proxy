import json
import logging
import os
import re
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, Boolean, DateTime, select, delete as sql_delete, func, and_, or_
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Configuration
PROXY_API_KEY = os.getenv("PROXY_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
DB_USER = os.getenv("DB_USER", "restdb_user")
DB_PASSWORD = os.getenv("DB_PASSWORD", "restdb_pass")
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
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RequestModel(Base):
    __tablename__ = "requests"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    videoId = Column(String(255), nullable=False)
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
async def check_api_key(x_apikey: Optional[str] = Header(None)):
    if PROXY_API_KEY and x_apikey != PROXY_API_KEY:
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
@app.on_event("startup")
async def startup_event():
    """Create tables on startup (non-blocking, continues even on error)."""
    try:
        logger.info(f"Attempting to connect to database at {DB_HOST}:{DB_PORT}/{DB_NAME}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created/verified successfully")
    except Exception as e:
        logger.warning(f"Database initialization warning (app will continue): {e}")
        logger.debug(f"Startup error details:\n{traceback.format_exc()}")
        # Continue anyway - tables will be created on first request if needed


# Health check
@app.get("/health")
async def health_check(session: AsyncSession = Depends(get_session)):
    """Health check endpoint - no API key required."""
    try:
        await session.execute(select(1))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {"status": "error", "database": "failed", "error": str(e)}


# ============================================================
# Generic REST API endpoints for collections (queue, requests)
# ============================================================

def get_model_by_collection(collection: str):
    """Return the model class for a collection name."""
    models = {"queue": Queue, "requests": RequestModel}
    return models.get(collection)


@app.get("/rest/{collection}")
async def get_collection(
    collection: str,
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
async def get_collection_item(
    collection: str,
    item_id: str,
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
async def post_collection(
    collection: str,
    request: Request,
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
                    kwargs[col.name] = item[col.name]
            
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in POST /rest/{collection}: {e}\n{traceback.format_exc()}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.put("/rest/{collection}/{item_id}")
async def put_collection_item(
    collection: str,
    item_id: str,
    request: Request,
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in PUT /rest/{collection}/{item_id}: {e}\n{traceback.format_exc()}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.patch("/rest/{collection}/{item_id}")
async def patch_collection_item(
    collection: str,
    item_id: str,
    request: Request,
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
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in PATCH /rest/{collection}/{item_id}: {e}\n{traceback.format_exc()}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.delete("/rest/{collection}/*")
async def delete_collection_bulk(
    collection: str,
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
            except:
                pass
        
        await session.commit()
        return {"deleted": deleted_count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in DELETE /rest/{collection}/*: {e}\n{traceback.format_exc()}")
        await session.rollback()
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")


@app.delete("/rest/{collection}/{item_id}")
async def delete_collection_item(
    collection: str,
    item_id: str,
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
            "collections": ["queue", "requests"],
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


# Backward compatibility: old endpoints for queue/requests
# These now use the generic REST endpoints internally

@app.get("/queue")
async def get_queue_compat(
    q: Optional[str] = None,
    h: Optional[str] = None,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """GET /queue - backward compatibility for /rest/queue"""
    return await get_collection("queue", q, h, _, session)


@app.post("/queue", status_code=201)
async def post_queue_compat(
    request: Request,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """POST /queue - backward compatibility for /rest/queue"""
    return await post_collection("queue", request, _, session)


@app.delete("/queue/{item_id}")
async def delete_queue_compat(
    item_id: str,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /queue/{item_id} - backward compatibility for /rest/queue/{item_id}"""
    return await delete_collection_item("queue", item_id, _, session)


@app.get("/requests")
async def get_requests_compat(
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """GET /requests - backward compatibility for /rest/requests"""
    return await get_collection("requests", None, None, _, session)


@app.post("/requests", status_code=201)
async def post_requests_compat(
    request: Request,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """POST /requests - backward compatibility for /rest/requests"""
    return await post_collection("requests", request, _, session)


@app.delete("/requests/*")
async def delete_requests_bulk_compat(
    q: Optional[str] = None,
    request: Request = None,
    _: Any = Depends(check_api_key),
    session: AsyncSession = Depends(get_session),
):
    """DELETE /requests/* - backward compatibility for /rest/requests/*"""
    return await delete_collection_bulk("requests", q, request, _, session)

