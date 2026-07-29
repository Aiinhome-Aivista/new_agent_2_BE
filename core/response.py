import json
# pyrefly: ignore [missing-import]
from fastapi import Request
# pyrefly: ignore [missing-import]
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

class APIStandardResponseMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/docs") or path.startswith("/redoc") or path.endswith("/openapi.json"):
            return await call_next(request)

        response = await call_next(request)
        
        # Intercept only application/json responses
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response
            
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
            
        if not body:
            return response
            
        try:
            original_data = json.loads(body)
        except Exception:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=content_type
            )
            
        success = 200 <= response.status_code < 300
        status_str = "success" if success else "error"
        
        message = ""
        data = None
        
        if isinstance(original_data, dict):
            if "status" in original_data and "statuscode" in original_data and "success" in original_data:
                return Response(
                    content=body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=content_type
                )
                
            message = original_data.get("message", "")
            data = original_data.get("data", None)
            
            if "detail" in original_data:
                detail = original_data["detail"]
                if isinstance(detail, list):
                    message = "Validation failed"
                    data = detail
                else:
                    message = str(detail)
            
            if data is None:
                clean_data = {k: v for k, v in original_data.items() if k not in ["success", "message", "status", "statuscode", "detail"]}
                if clean_data:
                    data = clean_data
        else:
            data = original_data
            
        if not message:
            message = "Operation completed successfully" if success else "An error occurred during request execution"
            
        standard_content = {}
        if data is not None:
            standard_content["data"] = data
        standard_content["message"] = message
        if "detail" in original_data:
            standard_content["detail"] = original_data["detail"]
        elif not success:
            standard_content["detail"] = message
        standard_content["success"] = success
        standard_content["status"] = status_str
        standard_content["statuscode"] = response.status_code
            
        new_body = json.dumps(standard_content).encode("utf-8")
        
        headers = dict(response.headers)
        headers["content-length"] = str(len(new_body))
        
        return Response(
            content=new_body,
            status_code=response.status_code,
            headers=headers,
            media_type="application/json"
        )
